import json,tempfile,unittest,io,wave
from pathlib import Path
from linux_native.inbox import DurableInbox
from linux_native.replies import ReplyQueue
from linux_native.voice_replies import VoiceReplies,reply_mode

class VoiceReplyTests(unittest.TestCase):
 def test_mode_tracks_input(self):
  self.assertEqual(reply_mode('auto',{'source_type':34}),'voice')
  self.assertEqual(reply_mode('auto',{'type':1}),'text')
  self.assertEqual(reply_mode('both',{}),'both')
 def test_voice_confirmation_requires_room_duration_and_watermark(self):
  with tempfile.TemporaryDirectory() as folder:
   box=DurableInbox(Path(folder)/'q.sqlite','self',{'123@chatroom'});q=ReplyQueue(box)
   box.ingest('s','123@chatroom',[]);box.ingest('s','123@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=1,text='hi')]);m=q.claim();q.stage(m,'direct','hello')
   voices=VoiceReplies(q);voices.request('123@chatroom','101','voice');buf=io.BytesIO()
   with wave.open(buf,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(b'\0\0'*24000)
   voices.ready('123@chatroom','101',buf.getvalue());voices.begin('123@chatroom','101',10)
   proof=dict(chat_id='123@chatroom',type=34,is_self=True,local_id=11,server_id='202',voice_duration_ms=1200)
   with self.assertRaises(ValueError):voices.confirm('123@chatroom','101',dict(proof,chat_id='wrong'))
   with self.assertRaises(ValueError):voices.confirm('123@chatroom','101',dict(proof,voice_duration_ms=50000))
   voices.confirm('123@chatroom','101',proof)
   self.assertEqual(q.get('123@chatroom','101')['state'],'sent')
   self.assertEqual(voices.get('123@chatroom','101')['state'],'sent')
   box.close()

 def test_disabled_tts_does_not_consume_attempts(self):
  with tempfile.TemporaryDirectory() as folder:
   state=Path(folder);box=DurableInbox(state/'q.sqlite','self',{'123@chatroom'});q=ReplyQueue(box)
   box.ingest('s','123@chatroom',[]);box.ingest('s','123@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=1,text='hi')]);m=q.claim();q.stage(m,'direct','hello',media_mode='voice')
   (state/'tts_config.json').write_text('{"enabled":false}')
   voices=VoiceReplies(q)
   for _ in range(4):voices.synthesize_one(state,lambda:True)
   self.assertEqual(voices.get('123@chatroom','101')['state'],'pending')
   self.assertEqual(box.db.execute('SELECT attempts FROM voice_replies').fetchone()[0],0)
   box.close()
 def test_switch_to_text_cancels_pending_voice_part(self):
  with tempfile.TemporaryDirectory() as folder:
   box=DurableInbox(Path(folder)/'q.sqlite','self',{'123@chatroom'});q=ReplyQueue(box)
   box.ingest('s','123@chatroom',[]);box.ingest('s','123@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=1,text='hi')]);m=q.claim();q.stage(m,'direct','hello',media_mode='voice')
   voices=VoiceReplies(q);voices.reconcile({'123@chatroom':{'reply_mode':'text'}})
   self.assertEqual(voices.get('123@chatroom','101')['state'],'cancelled')
   self.assertEqual(voices.get('123@chatroom','101')['mode'],'text')
   self.assertEqual(q.get('123@chatroom','101')['state'],'draft')
   box.close()

 def test_both_parts_keep_separate_receipts(self):
  with tempfile.TemporaryDirectory() as folder:
   box=DurableInbox(Path(folder)/'q.sqlite','self',{'123@chatroom'});q=ReplyQueue(box)
   try:
    box.ingest('s','123@chatroom',[]);box.ingest('s','123@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=1,text='hi')]);q.stage(q.claim(),'direct','hello',media_mode='both')
    q.begin_send('123@chatroom','101',1);q.confirm('123@chatroom','101',dict(chat_id='123@chatroom',text='hello',is_self=True,local_id=2,server_id='201'))
    buf=io.BytesIO()
    with wave.open(buf,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(b'\0\0'*24000)
    voices=VoiceReplies(q);voices.ready('123@chatroom','101',buf.getvalue());voices.begin('123@chatroom','101',2)
    voices.confirm('123@chatroom','101',dict(chat_id='123@chatroom',type=34,is_self=True,local_id=3,server_id='202',voice_duration_ms=1200))
    self.assertEqual(q.get('123@chatroom','101')['outgoing_id'],'201')
    self.assertEqual(voices.get('123@chatroom','101')['outgoing_id'],'202')
    self.assertEqual(box.db.execute('SELECT COUNT(*) FROM delivery_events').fetchone()[0],1)
   finally:box.close()
