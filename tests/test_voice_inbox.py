import threading,time,unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from wxbot_framework.sdk import Message
from wxbot_framework.voice_inbox import VoiceInbox

class VoiceInboxTests(unittest.TestCase):
    def message(self,**kw):
        return Message(text='',chat_id='123@chatroom',sender_id='sender',type=34,is_group=True,
                       session_id='s',data={'native_message_id':'123','monitor_only':True},**kw)
    def inbox(self,reader=None,current=lambda:True,client=None):
        event=threading.Event();received=[]
        def deliver(message):received.append(message);event.set()
        reader=reader or SimpleNamespace(get=Mock(return_value=b'audio'),close=Mock())
        client=client or SimpleNamespace(transcribe=Mock(return_value='你好'))
        inbox=VoiceInbox(reader,client,deliver,current,{'123@chatroom'},decode=lambda audio:b'wav')
        self.addCleanup(inbox.close)
        return inbox,event,received,reader,client
    def test_scope_self_duplicates_and_normalized_transcript(self):
        inbox,event,received,reader,client=self.inbox()
        message=self.message()
        self.assertFalse(inbox.submit(replace(message,chat_id='456@chatroom')))
        self.assertFalse(inbox.submit(replace(message,is_self=True)))
        self.assertTrue(inbox.submit(message))
        self.assertFalse(inbox.submit(message))
        self.assertTrue(event.wait(2))
        output=received[0]
        self.assertEqual(output.text,'你好');self.assertEqual(output.type,1)
        self.assertEqual(output.sender_id,'sender');self.assertEqual(output.data['source_type'],34)
        self.assertFalse(output.data['monitor_only'])
        reader.get.assert_called_once()
    def test_stale_session_never_runs_asr_or_delivers(self):
        inbox,event,received,reader,client=self.inbox(current=lambda:False)
        self.assertFalse(inbox.submit(self.message()))
        client.transcribe.assert_not_called();self.assertEqual(received,[])
    def test_provider_error_does_not_leak_or_turn_into_question(self):
        client=SimpleNamespace(transcribe=Mock(side_effect=RuntimeError('secret provider response')))
        inbox,event,received,reader,client=self.inbox(client=client)
        self.assertTrue(inbox.submit(self.message()));self.assertTrue(event.wait(2))
        output=received[0]
        self.assertEqual(output.type,34);self.assertTrue(output.data['monitor_only'])
        self.assertEqual(output.data['voice_status'],'failed')
        self.assertNotIn('secret',repr(dict(output.data)))
    def test_expired_message_not_submitted(self):
        inbox,*_=self.inbox()
        self.assertFalse(inbox.submit(replace(self.message(),received_at=time.monotonic()-61)))
