import tempfile
import unittest
from pathlib import Path
from linux_native.inbox import DurableInbox
from linux_native.replies import ReplyQueue


class LinuxReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'inbox.sqlite'
        self.box=DurableInbox(self.path,'self',{'bot@chatroom'})
        self.box.ingest('s','bot@chatroom',[])
        self.box.ingest('s','bot@chatroom',[{'local_id':1,'server_id':'101','sender_id':'other','type':1,'text':'你好'}])
        self.queue=ReplyQueue(self.box)

    def tearDown(self):self.box.close();self.tmp.cleanup()

    def test_claim_is_exclusive_and_model_work_can_recover(self):
        first=self.queue.claim();self.assertEqual(first['server_id'],'101')
        self.assertIsNone(self.queue.claim())
        self.queue.recover();self.assertEqual(self.queue.claim()['server_id'],'101')

    def test_sending_is_not_retried_after_restart(self):
        msg=self.queue.claim()
        self.queue.stage(msg,'direct','你好呀')
        self.queue.begin_send('bot@chatroom','101',0)
        self.box.close();self.box=DurableInbox(self.path,'self',{'bot@chatroom'})
        self.queue=ReplyQueue(self.box);self.queue.recover()
        self.assertEqual(self.queue.get('bot@chatroom','101')['state'],'uncertain')
        with self.assertRaises(ValueError):self.queue.begin_send('bot@chatroom','101',0)
        self.assertIsNone(self.queue.claim())

    def test_confirmation_requires_same_room_text_self_and_server_id(self):
        msg=self.queue.claim();self.queue.stage(msg,'direct','你好呀');self.queue.begin_send('bot@chatroom','101',9)
        for change in ({'chat_id':'wrong@chatroom'},{'text':'wrong'},{'is_self':False},{'server_id':'0'},{'local_id':8}):
            proof=dict(chat_id='bot@chatroom',text='你好呀',is_self=True,server_id='201',local_id=10);proof.update(change)
            with self.assertRaises(ValueError):self.queue.confirm('bot@chatroom','101',proof)
        self.queue.confirm('bot@chatroom','101',dict(chat_id='bot@chatroom',text='你好呀',is_self=True,server_id='201',local_id=10))
        self.assertEqual(self.queue.get('bot@chatroom','101')['state'],'sent')

    def test_retry_backoff_is_bounded_and_persistent(self):
        for attempt,now in enumerate((100,105,115),1):
            msg=self.queue.claim(now=now)
            self.assertIsNotNone(msg)
            self.queue.fail_model(msg,retryable=True,now=now)
            self.assertIsNone(self.queue.claim(now=now))
        self.queue.recover()
        self.assertIsNone(self.queue.claim(now=10000))

    def test_same_outgoing_cannot_confirm_two_sources(self):
        self.box.ingest('s','bot@chatroom',[{'local_id':2,'server_id':'102','sender_id':'other','type':1,'text':'again'}])
        for source in ('101','102'):
            msg=self.queue.claim();self.queue.stage(msg,'direct','same')
            self.queue.begin_send('bot@chatroom',source,9)
            proof=dict(chat_id='bot@chatroom',text='same',is_self=True,server_id='201',local_id=10)
            if source=='101':self.queue.confirm('bot@chatroom',source,proof)
            else:
                with self.assertRaises(ValueError):self.queue.confirm('bot@chatroom',source,proof)

    def test_ignore_completes_without_outgoing_draft(self):
        msg=self.queue.claim();self.queue.stage(msg,'ignore','')
        self.assertEqual(self.queue.get('bot@chatroom','101')['state'],'ignored')
        self.assertIsNone(self.queue.claim())
