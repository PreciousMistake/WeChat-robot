"""Real SQLite membership snapshots must never turn incomplete reads into exits."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from linux_native.group_contacts import ContactGroups

BOT = 'wxid_bot'
ALICE = 'wxid_alice'
BOB = 'wxid_bob'
ROOM = '100@chatroom'


class Events:
    def __init__(self):
        self.values = {}

    def emit(self, key, payload):
        self.values.setdefault(key, payload)
        return payload


class ContactGroupsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.events = Events()
        self.groups = ContactGroups(self.temp.name, BOT, self.events)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE contact(id INTEGER PRIMARY KEY,username TEXT,nick_name TEXT,remark TEXT);
            CREATE TABLE chat_room(id INTEGER PRIMARY KEY,username TEXT,owner TEXT,ext_buffer BLOB);
            CREATE TABLE chatroom_member(room_id INTEGER,member_id INTEGER);
            INSERT INTO contact VALUES(1,'wxid_bot','Bot',''),(2,'wxid_alice','Alice','Alias'),(3,'wxid_bob','Bob','');
            INSERT INTO chat_room VALUES(10,'100@chatroom','wxid_alice',NULL);
            INSERT INTO chatroom_member VALUES(10,1),(10,2);
        """)

    def snap(self, *members):
        return {ROOM: {member: {'nickname': member, 'alias': '', 'role': 'member'} for member in members}}

    def test_read_uses_contact_identity_and_owner_only(self):
        self.assertEqual(self.groups.read(self.db, [ROOM]), {ROOM: {
            BOT: {'nickname': 'Bot', 'alias': '', 'role': 'member'},
            ALICE: {'nickname': 'Alice', 'alias': 'Alias', 'role': 'owner'},
        }})
        self.assertEqual(self.groups.read(self.db, []), {})

    def test_read_rejects_orphan_member_rows_and_missing_self(self):
        self.db.execute('INSERT INTO chatroom_member VALUES(10,99)')
        self.assertEqual(self.groups.read(self.db, [ROOM]), {})
        self.db.execute('DELETE FROM chatroom_member WHERE member_id IN (99,1)')
        self.assertEqual(self.groups.read(self.db, [ROOM]), {})

    def test_baseline_then_two_confirmations_emit_real_identity(self):
        baseline = self.snap(BOT, ALICE)
        joined = self.snap(BOT, ALICE, BOB)
        self.assertEqual(self.groups.update(baseline, [ROOM]), [])
        self.assertEqual(self.groups.update(joined, [ROOM]), [])
        emitted = self.groups.update(joined, [ROOM])
        self.assertEqual(len(emitted), 1)
        event = next(iter(self.events.values.values()))
        self.assertEqual(event['sender_id'], BOB)
        self.assertEqual(event['chat_id'], ROOM)
        self.assertEqual(event['type'], 10002)
        self.assertEqual(event['text'], '')
        self.assertEqual(event['group_event'], {'room_id': ROOM, 'member_id': BOB,
            'nickname': BOB, 'kind': 'join', 'source': 'membership'})
        self.assertEqual(self.groups.update(joined, [ROOM]), [])

    def test_invalid_snapshot_breaks_confirmation_and_never_mass_leaves(self):
        self.groups.update(self.snap(BOT, ALICE), [ROOM])
        changed = self.snap(BOT, ALICE, BOB)
        self.groups.update(changed, [ROOM])
        self.groups.update({}, [ROOM])
        self.assertEqual(self.groups.update(changed, [ROOM]), [])
        self.assertEqual(len(self.groups.update(changed, [ROOM])), 1)
        for bad in ({}, {ROOM: {}}, self.snap(ALICE)):
            self.groups.update(bad, [ROOM])
            self.assertEqual(self.groups.update(bad, [ROOM]), [])
        self.assertEqual(len(self.events.values), 1)

    def test_disabled_room_reenable_is_new_baseline(self):
        self.groups.update(self.snap(BOT, ALICE), [ROOM])
        self.groups.update({}, [])
        self.groups.update(self.snap(BOT, BOB), [ROOM])
        self.groups.update(self.snap(BOT, BOB), [ROOM])
        self.assertEqual(self.events.values, {})

    def test_repeated_same_join_leave_has_distinct_durable_keys(self):
        base, joined = self.snap(BOT, ALICE), self.snap(BOT, ALICE, BOB)
        self.groups.update(base, [ROOM])
        for changed in (joined, base, joined, base):
            self.groups.update(changed, [ROOM])
            self.groups.update(changed, [ROOM])
        self.assertEqual(len(self.events.values), 4)
        replacement = ContactGroups(self.temp.name, BOT, self.events)
        replacement.update(base, [ROOM])
        replacement.update(joined, [ROOM])
        replacement.update(joined, [ROOM])
        self.assertEqual(len(self.events.values), 5)

    def test_pending_event_survives_emit_failure_and_retries_once(self):
        baseline, joined = self.snap(BOT, ALICE), self.snap(BOT, ALICE, BOB)
        self.groups.update(baseline, [ROOM])
        self.groups.update(joined, [ROOM])
        def fail(key, payload):
            raise OSError('queue unavailable')
        self.groups.event_store = type('Unavailable', (), {'emit': staticmethod(fail)})()
        with self.assertRaises(OSError):
            self.groups.update(joined, [ROOM])
        replacement = ContactGroups(self.temp.name, BOT, self.events)
        self.assertEqual(len(replacement.update(joined, [ROOM])), 1)
        self.assertEqual(len(self.events.values), 1)
        self.assertEqual(replacement.update(joined, [ROOM]), [])

    def test_pending_events_from_disabled_scope_are_not_emitted(self):
        baseline, joined = self.snap(BOT, ALICE), self.snap(BOT, ALICE, BOB)
        self.groups.update(baseline, [ROOM])
        self.groups.update(joined, [ROOM])
        def fail(key, payload):
            raise OSError('queue unavailable')
        self.groups.event_store = type('Unavailable', (), {'emit': staticmethod(fail)})()
        with self.assertRaises(OSError):
            self.groups.update(joined, [ROOM])
        replacement = ContactGroups(self.temp.name, BOT, self.events)
        self.assertEqual(replacement.update({}, []), [])
        self.assertEqual(self.events.values, {})

    def test_cache_contains_only_enabled_confirmed_members_and_owner(self):
        baseline = self.groups.read(self.db, [ROOM])
        self.groups.update(baseline, [ROOM])
        path = Path(self.temp.name) / 'group_members.json'
        cache = json.loads(path.read_text())
        self.assertEqual(cache['account'], BOT)
        self.assertEqual(next(row for row in cache['groups'][ROOM] if row['wxid'] == ALICE), {'wxid': ALICE, 'nickname': 'Alice',
            'alias': 'Alias', 'role': 'owner'})
        if os.name != 'nt':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.groups.update(self.snap(BOT, ALICE, BOB), [ROOM])
        self.assertEqual(len(json.loads(path.read_text())['groups'][ROOM]), 2)
        self.groups.update({}, [])
        self.assertEqual(json.loads(path.read_text())['groups'], {})


if __name__ == '__main__':
    unittest.main()
