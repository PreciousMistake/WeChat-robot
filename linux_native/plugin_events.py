"""Account-bound plugin observations for native Linux WCDB snapshots.

Only the main worker's enabled rooms should be observed. Revisit a bounded recent
window on every poll: native recall mutates an existing message row in place.
No network calls, native process reads or image decryption are performed here.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import xml.etree.ElementTree as ET

MAX_RAW_BYTES = 65536
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_SCAN_FILES = 512
MAX_SCAN_BYTES = 64 * 1024 * 1024


def _xml(raw):
    if not isinstance(raw, str) or len(raw) > MAX_RAW_BYTES:
        return None
    try:
        if len(raw.encode('utf-8')) > MAX_RAW_BYTES or '\0' in raw or re.search(r'<!\s*(DOCTYPE|ENTITY)\b', raw, re.I):
            return None
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError, UnicodeError):
        return None
    stack = [(root, 1)]; count = 0
    while stack:
        element, depth = stack.pop(); count += 1
        if count > 256 or depth > 16 or len(element.attrib) > 64:
            return None
        stack.extend((child, depth + 1) for child in element)
    return root


def _is_recall(raw):
    root = _xml(raw)
    return (root is not None and root.tag == 'sysmsg' and
            root.get('type') == 'revokemsg' and len(root.findall('revokemsg')) == 1)


def parse_file_metadata(raw):
    """Return only public file identity fields; discard all CDN credentials."""
    if _xml(raw) is None:
        return None
    from wxbot_framework.file_metadata import parse_file_metadata as parse, FileMetadataError
    try:
        metadata = parse(raw)
    except (FileMetadataError, ValueError, TypeError):
        return None
    if metadata is None or not metadata.md5 or metadata.size is None:
        return None
    return {'type': 49, 'app_type': 6, 'md5': metadata.md5.lower(),
            'size': metadata.size, 'file_size': metadata.size,
            'file_name': metadata.title, 'extension': metadata.extension}


def _safe_payload(row, room):
    payload = {key: row[key] for key in ('local_id', 'sender_id', 'type', 'app_type', 'create_time', 'file_path', 'file_status', 'file_name', 'image_path', 'image_status', 'md5', 'file_size') if key in row}
    payload.update(chat_id=room, server_id=str(row.get('server_id', '')),
                   native_message_id=str(row.get('native_message_id', row.get('server_id', ''))),
                   text=row.get('text', '')[:4000] if isinstance(row.get('text'), str) else '')
    if payload.get('type') != 1:
        raw = row.get('raw_content')
        if isinstance(raw, str):
            try:
                if len(raw.encode('utf-8')) <= MAX_RAW_BYTES:
                    payload['raw_content'] = raw
            except UnicodeError:
                pass
    return payload


class EventStore:
    """Durable event queue and original-message cache, with a separate owner DB."""
    def __init__(self, state, account):
        path = Path(state) / 'plugin_events.sqlite'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.account = account
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS owner(account TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS sources(
                account TEXT, stream TEXT, room TEXT, local_id INTEGER,
                server_id TEXT, current_type INTEGER, original_payload TEXT,
                PRIMARY KEY(account,stream,room,local_id));
            CREATE TABLE IF NOT EXISTS events(
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0);
        ''')
        owner = self.db.execute('SELECT account FROM owner').fetchone()
        if owner and owner[0] != account:
            self.db.close()
            raise ValueError('Plugin event store belongs to another account')
        with self.db:
            if not owner:
                self.db.execute('INSERT INTO owner VALUES(?)', (account,))

    def observe(self, stream, room, rows, baseline=False):
        rows = list(rows)
        if len(rows) > 10000:
            raise ValueError('Observation batch too large')
        count = 0
        with self.db:
            for row in rows:
                local_id = row.get('local_id'); kind = row.get('type')
                server = str(row.get('server_id', ''))
                if (type(local_id) is not int or local_id < 0 or type(kind) is not int or
                        not server.isascii() or not server.isdecimal() or not 0 < int(server) < 2**64):
                    continue
                payload = _safe_payload(row, room)
                key = (self.account, stream, room, local_id)
                old = self.db.execute('SELECT server_id,current_type,original_payload FROM sources WHERE account=? AND stream=? AND room=? AND local_id=?', key).fetchone()
                original = json.loads(old[2]) if old and old[0] == server else None
                recall = (old and old[0] == server and old[1] not in (10000, 10002) and
                          kind == 10000 and original and original.get('type') not in (10000, 10002) and
                          _is_recall(payload.get('raw_content')))
                ready_changed=bool(original and any(payload.get(k)=='ready' and original.get(k)!='ready' for k in ('file_status','image_status')))
                media = kind not in (1, 34, 10000, 10002) and (not old or old[0] != server or ready_changed)
                if not baseline and (recall or media):
                    event_key = json.dumps([*key, server, 'recall' if recall else ('media-ready' if ready_changed else 'media')], separators=(',', ':'))
                    ident = hashlib.sha256(event_key.encode()).hexdigest()
                    if recall:
                        event_number = (int(ident[:16], 16) & ((1 << 63) - 1)) or 1
                        event_payload = dict(chat_id=room, server_id=event_number,
                            sender_id=original.get('sender_id', ''), type=10000, text='',
                            native_message_id=ident, recall={'chat_id': room, 'message_id': server})
                    else:
                        event_payload = payload
                    count += self.db.execute('INSERT OR IGNORE INTO events(id,payload) VALUES(?,?)',
                        (ident, json.dumps(event_payload, ensure_ascii=False))).rowcount
                # Keep the first ordinary payload through a native row's recall mutation.
                cache = original if original and kind in (10000, 10002) else payload
                self.db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?) ON CONFLICT(account,stream,room,local_id) DO UPDATE SET server_id=excluded.server_id,current_type=excluded.current_type,original_payload=excluded.original_payload',
                    (*key, server, kind, json.dumps(cache, ensure_ascii=False)))
        return count

    def emit(self, key, payload):
        ident=hashlib.sha256((self.account+'\0'+key).encode()).hexdigest()
        number=(int(ident[:16],16)&((1<<63)-1)) or 1
        payload=dict(payload,server_id=str(number),native_message_id=str(number))
        with self.db:
            return self.db.execute('INSERT OR IGNORE INTO events(id,payload) VALUES(?,?)',(ident,json.dumps(payload,ensure_ascii=False))).rowcount

    def pending(self, limit=100):
        limit = max(0, min(int(limit), 1000))
        return [{'id': ident, 'payload': json.loads(payload)} for ident, payload in
                self.db.execute('SELECT id,payload FROM events WHERE done=0 ORDER BY rowid LIMIT ?', (limit,))]

    def finish(self, ident):
        with self.db:
            self.db.execute('UPDATE events SET done=1 WHERE id=?', (ident,))

    def originals(self, limit=1000):
        """Restore plugin caches before dispatch; callers must filter enabled rooms."""
        limit = max(0, min(int(limit), 10000))
        result = []
        for (encoded,) in self.db.execute(
                'SELECT original_payload FROM sources WHERE account=? ORDER BY rowid DESC', (self.account,)):
            payload = json.loads(encoded)
            if payload.get('type') in (10000, 10002):
                continue
            if len(result) >= limit:
                break
            result.append(payload)
        return result

    def close(self):
        self.db.close()


def resolve_file(account_root, metadata):
    """Find one verified local file in one month, within strict I/O budgets.

    Metadata must carry type=49, app_type=6, md5, size/file_size, file_name,
    and month=YYYY-MM (or a create_time Unix timestamp). Never resolve by name.
    """
    if not isinstance(metadata, dict) or metadata.get('type') != 49 or metadata.get('app_type') != 6:
        return None
    digest = metadata.get('md5'); size = metadata.get('size', metadata.get('file_size'))
    name = metadata.get('file_name', '')
    if (not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{32}', digest) or
            type(size) is not int or not 0 < size <= MAX_FILE_BYTES or not isinstance(name, str)):
        return None
    suffix = Path(name.replace('\\', '/')).suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,16}', suffix):
        return None
    month = metadata.get('month')
    if month is None:
        try:
            month = datetime.datetime.fromtimestamp(metadata['create_time'],
                datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m')
        except (KeyError, ValueError, TypeError, OverflowError, OSError):
            return None
    if not isinstance(month, str) or not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', month):
        return None
    root = Path(account_root)
    directory = root / 'msg' / 'file' / month
    try:
        for component in (root, root/'msg', root/'msg'/'file', directory):
            if component.is_symlink() or getattr(component, 'is_junction', lambda: False)():
                return None
        resolved_root = root.resolve(strict=True)
        if not directory.resolve(strict=True).is_relative_to(resolved_root):
            return None
        matches = []; scanned = 0; read_bytes = 0
        with os.scandir(directory) as entries:
            for entry in entries:
                scanned += 1
                if scanned > MAX_SCAN_FILES:
                    return None
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                candidate = Path(entry.path)
                if candidate.suffix.lower() != suffix or entry.stat(follow_symlinks=False).st_size != size:
                    continue
                if read_bytes + size > MAX_SCAN_BYTES:
                    return None
                flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
                fd = os.open(candidate, flags)
                with os.fdopen(fd, 'rb') as source:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                        continue
                    hasher = hashlib.md5(); remaining = size
                    while remaining:
                        block = source.read(min(65536, remaining))
                        if not block:
                            return None
                        read_bytes += len(block); remaining -= len(block); hasher.update(block)
                    if source.read(1):
                        return None
                    if hasher.hexdigest() == digest.lower():
                        matches.append(candidate)
                        if len(matches) > 1:
                            return None
        return matches[0] if len(matches) == 1 else None
    except (OSError, ValueError):
        return None
