import tempfile
import unittest
from pathlib import Path
from linux_native.inbox import DurableInbox
from linux_native.asr_worker import process_audio


class ASRWorkerTests(unittest.TestCase):
    def test_transcript_has_original_identity_and_is_not_dispatched_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            box=DurableInbox(Path(folder)/'q.sqlite','self',{'bot@chatroom'});box.voice_groups={'bot@chatroom'}
            box.ingest('s','bot@chatroom',[])
            box.ingest('s','bot@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=34,text='')])
            try:
                result=process_audio(box,lambda message:b'silk',lambda data:b'wav',lambda wav:'鲸鱼娘你好')
                self.assertEqual(result['state'],'recognized')
                self.assertEqual(box.pending()[0]['server_id'],'101')
                self.assertIsNone(process_audio(box,lambda m:None,lambda a:None,lambda a:None))
            finally:box.close()

    def test_inflight_configuration_change_cancels_transcript(self):
        with tempfile.TemporaryDirectory() as folder:
            from linux_native.asr_worker import configuration_guard
            state=Path(folder)
            (state/'ai_config.json').write_text('{}')
            (state/'asr_config.json').write_text('{}')
            guard=configuration_guard(state,lambda:True)
            self.assertTrue(guard())
            (state/'asr_config.json').write_text('{"enabled":false}')
            self.assertFalse(guard())
