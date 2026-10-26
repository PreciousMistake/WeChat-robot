import tempfile
import unittest
from pathlib import Path
from linux_native.inbox import DurableInbox


class AudioQueueTests(unittest.TestCase):
    def test_voice_is_queued_once_and_text_waits_for_transcription(self):
        with tempfile.TemporaryDirectory() as folder:
            box=DurableInbox(Path(folder)/'q.sqlite','self',{'bot@chatroom'})
            box.voice_groups={'bot@chatroom'}
            box.ingest('s','bot@chatroom',[])
            row=dict(local_id=1,server_id='101',sender_id='other',type=34,text='')
            box.ingest('s','bot@chatroom',[row]);box.ingest('s','bot@chatroom',[row])
            try:
                self.assertEqual(box.pending(),[])
                claimed=box.claim_audio(now=100)
                self.assertEqual(claimed['server_id'],'101')
                self.assertIsNone(box.claim_audio(now=100))
                box.finish_audio(claimed,'今天测试语音')
                self.assertEqual(box.pending()[0]['text'],'今天测试语音')
                self.assertEqual(box.pending()[0]['source_type'],34)
            finally:box.close()

    def test_disabled_voice_does_not_later_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            box=DurableInbox(Path(folder)/'q.sqlite','self',{'bot@chatroom'})
            box.ingest('s','bot@chatroom',[])
            box.ingest('s','bot@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=34,text='')])
            box.voice_groups={'bot@chatroom'}
            try:self.assertIsNone(box.claim_audio(now=100))
            finally:box.close()
