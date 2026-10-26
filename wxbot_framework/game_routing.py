"""Short, non-network routing decisions before asynchronous game handlers run."""
import threading

class GameRouting:
    def __init__(self,starts,exits,controls,answer,*,controls_without_game=True):
        self.starts,self.exits,self.controls=frozenset(starts),frozenset(exits),frozenset(controls)
        self.answer=answer;self.session=None;self.desired={};self.pending={}
        self.controls_without_game=controls_without_game
        self.lock=threading.RLock()

    def _session(self,message):
        if self.session!=message.session_id:
            self.session=message.session_id;self.desired.clear();self.pending.clear()

    def release(self,message):
        with self.lock:
            self._session(message);self.desired[message.chat_id]=False

    def claim(self,message,game_active):
        if message.type!=1 or not message.is_group or message.is_self or message.data.get('is_at_self'):
            return False
        text=message.text.strip();room=message.chat_id
        with self.lock:
            self._session(message)
            if text in self.starts:
                self.desired[room]=True;self.pending[room]=self.pending.get(room,0)+1
                return True
            if text in self.exits:
                self.desired[room]=False
                return True
            if text in self.controls:return self.controls_without_game or self.desired.get(room,game_active)
            return bool(text and self.desired.get(room,game_active) and self.answer(text))

    def complete(self,message,game_active):
        with self.lock:
            if message.session_id!=self.session or message.text.strip() not in self.starts:return
            room=message.chat_id
            self.pending[room]=max(0,self.pending.get(room,0)-1)
            if not self.pending[room] and not game_active:self.desired[room]=False
