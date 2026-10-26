"""Confirmed group membership from complete native contact DB snapshots only."""
from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from wxbot_framework.account import validate_account
from wxbot_framework.group_events import GroupMembershipTracker, GroupEventError
from wxbot_framework.text import validate_target


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=True, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _room(value):
    try:
        validate_target(value)
        return value.endswith('@chatroom')
    except (ValueError, TypeError):
        return False


def _snapshot(members, account):
    if not isinstance(members, Mapping) or account not in members or not 1 <= len(members) <= 10000:
        return None
    checked = {}
    try:
        for member, detail in members.items():
            validate_account(member)
            if member.startswith(('v3_', 'v4_')) or not isinstance(detail, Mapping):
                return None
            fields = {}
            for field in ('nickname', 'alias'):
                value = detail.get(field, '')
                if (not isinstance(value, str) or len(value) > 256 or
                    len(value.encode('utf-8')) > 1024 or
                    any(ord(c) < 32 or ord(c) == 127 for c in value)):
                    return None
                fields[field] = value.strip()
            fields['role'] = 'owner' if detail.get('role') == 'owner' else 'member'
            checked[member] = fields
    except (ValueError, TypeError, UnicodeError):
        return None
    return checked


class ContactGroups:
    def __init__(self, state, account, event_store):
        validate_account(account)
        self.state = Path(state)
        self.account = account
        self.event_store = event_store
        self.trackers = {}
        self.details = {}
        self.journal_path = self.state / 'group_members_events.json'
        self.journal = {'account': account, 'heads': {}, 'pending': {}}
        if self.journal_path.exists():
            self.journal = json.loads(self.journal_path.read_text(encoding='utf-8'))
            if self.journal.get('account') != account:
                raise ValueError('Group membership state belongs to another account')

    def read(self, db, rooms):
        """Read the complete joined relation; any unresolved row invalidates its room.

        ext_buffer is deliberately not decoded or inferred as a source of members.
        The decrypted db must represent one complete, consistent native snapshot.
        """
        result = {}
        for room in sorted(set(rooms)):
            if not _room(room):
                continue
            try:
                records = db.execute('SELECT id,owner FROM chat_room WHERE username=?', (room,)).fetchall()
                if len(records) != 1:
                    continue
                room_id, owner = records[0]
                rows = db.execute("""SELECT c.username,c.nick_name,c.remark
                    FROM chatroom_member AS m LEFT JOIN contact AS c ON c.id=m.member_id
                    WHERE m.room_id=? LIMIT 10001""", (room_id,)).fetchall()
                if not rows or len(rows) > 10000 or any(not row[0] for row in rows):
                    continue
                members = {member: {'nickname': name or '', 'alias': alias or '',
                    'role': 'owner' if member == owner else 'member'} for member, name, alias in rows}
                if len(members) != len(rows):
                    continue
                checked = _snapshot(members, self.account)
                if checked is not None:
                    result[room] = checked
            except sqlite3.Error:
                continue
        return result

    def _flush(self):
        emitted = []
        for key, payload in list(self.journal['pending'].items()):
            self.event_store.emit(key, payload)
            emitted.append(payload)
            del self.journal['pending'][key]
            _write(self.journal_path, self.journal)
        return emitted

    def update(self, snapshot, enabled_rooms):
        """Only consecutive complete snapshots advance the live account baseline."""
        enabled = {room for room in enabled_rooms if _room(room)}
        for room in set(self.trackers) - enabled:
            self.trackers.pop(room).close()
            self.details.pop(room, None)
        pending = self.journal['pending']
        retained = {key: payload for key, payload in pending.items() if payload['chat_id'] in enabled}
        if len(retained) != len(pending):
            self.journal['pending'] = retained
            _write(self.journal_path, self.journal)
        emitted = self._flush()
        for room in sorted(enabled):
            if room not in self.trackers:
                self.trackers[room] = GroupMembershipTracker(
                    self.account, 'linux:' + self.account, confirmations=2)
            tracker = self.trackers[room]
            members = _snapshot(snapshot.get(room), self.account) if isinstance(snapshot, Mapping) else None
            before = tracker.members(room)
            try:
                events = tracker.update(room, None if members is None else {
                    member: detail['nickname'] for member, detail in members.items()})
            except GroupEventError:
                continue
            if members is None:
                continue
            confirmed = tracker.members(room)
            old_details = self.details.get(room, {})
            self.details[room] = {member: dict(members.get(member, old_details.get(member,
                {'nickname': name, 'alias': '', 'role': 'member'})), nickname=name)
                for member, name in confirmed.items()}
            if not events:
                continue
            old_head = self.journal['heads'].get(room, _digest([self.account, room, 'baseline']))
            head = _digest([old_head, sorted(before), sorted(confirmed)])
            self.journal['heads'][room] = head
            for event in events:
                key = 'membership:' + _digest([self.account, room, head, event.member_id, event.kind])
                self.journal['pending'][key] = {'type': 10002, 'chat_id': room,
                    'sender_id': event.member_id, 'text': '', 'group_event': {
                        'room_id': room, 'member_id': event.member_id, 'nickname': event.nickname,
                        'kind': event.kind, 'source': 'membership'}}
            _write(self.journal_path, self.journal)
            emitted.extend(self._flush())
        _write(self.state / 'group_members.json', {'account': self.account, 'groups': {
            room: [dict(wxid=member, **detail) for member, detail in sorted(members.items())]
            for room, members in self.details.items() if room in enabled}})
        return emitted
