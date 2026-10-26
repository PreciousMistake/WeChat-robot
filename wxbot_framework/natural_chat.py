"""Pure state for a single room's natural conversation (no network or sending)."""
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    kind: str
    reply: str

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or data.get('decision') not in ('direct', 'participate', 'ignore'):
            raise ValueError('Invalid decision')
        kind = data['decision']
        if kind == 'ignore':
            return cls(kind, '')
        reply = data.get('reply')
        if (not isinstance(reply, str) or not reply.strip() or len(reply) > 400 or
                any(ord(c) < 32 and c not in '\n\r\t' for c in reply)):
            raise ValueError('Invalid reply')
        return cls(kind, reply.strip())


class Conversation:
    def __init__(self, room, cooldown=30, hourly=30):
        self.room, self.cooldown, self.hourly = room, cooldown, hourly
        self.messages = deque(maxlen=12)
        self.seen = deque(maxlen=1024)
        self.proactive = deque()
        self.session = None
        self.generation = 0
        self.pending = False
        self.first_pending = self.last_incoming = 0
        self.last_request = float('-inf')

    def ingest(self, *, room, session, sender, text, ident, is_self, now, quote=None):
        if room != self.room or is_self or not text.strip() or text.lstrip().startswith('/'):
            return False
        if session != self.session:
            self.session = session
            self.messages.clear()
            self.seen.clear()
            self.pending = False
        key = (session, ident)
        if key in self.seen:
            return False
        self.seen.append(key)
        entry = {'speaker': sender[:128], 'text': text[:500], 'bot': False}
        if isinstance(quote, dict):
            entry['quote'] = {key: str(quote.get(key, ''))[:limit] for key, limit in
                              (('text', 500), ('author_id', 128), ('author_name', 128),
                               ('message_id', 32), ('original_type', 8))}
        self.messages.append(entry)
        self.generation += 1
        if not self.pending:
            self.first_pending = now
        self.pending, self.last_incoming = True, now
        return True

    def take(self, now):
        if self.pending and now-self.last_incoming > 45:
            self.pending = False
            return None
        if (not self.pending or now-self.last_request < 5 or
                (now-self.last_incoming < 3 and now-self.first_pending < 6)):
            return None
        self.pending = False
        self.last_request = now
        return {'generation':self.generation, 'session':self.session,
                'created':now, 'messages':list(self.messages)}

    def reset_session(self, session):
        self.session = session
        self.generation += 1
        self.messages.clear()
        self.seen.clear()
        self.pending = False

    def current(self, job, now):
        return (job['generation'] == self.generation and job['session'] == self.session and
                0 <= now-job['created'] <= 45)

    def allowed(self, kind, now):
        while self.proactive and now-self.proactive[0] >= 3600:
            self.proactive.popleft()
        if kind == 'direct':
            return True
        return (kind == 'participate' and len(self.proactive) < self.hourly and
                (not self.proactive or now-self.proactive[-1] >= self.cooldown))

    def sent(self, kind, text, now):
        if kind == 'participate':
            self.proactive.append(now)
        self.messages.append({'speaker':'机器人', 'text':text, 'bot':True})
