import tempfile
import unittest
from pathlib import Path
from linux_native.inbox import DurableInbox
from linux_native.replies import ReplyQueue
from linux_native.drafts import process_one


class DraftTests(unittest.TestCase):
    def test_model_result_is_persisted_as_draft_without_sending(self):
        with tempfile.TemporaryDirectory() as directory:
            box=DurableInbox(Path(directory)/'q.sqlite','self',{'bot@chatroom'})
            box.ingest('s','bot@chatroom',[])
            box.ingest('s','bot@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=1,text='鲸鱼娘你好')])
            queue=ReplyQueue(box)
            class Client:
                def complete_json(self,system,user):return {'decision':'direct','reply':'你好呀～'}
            result=process_one(queue,Client(),{'bot@chatroom':'角色设定'})
            self.assertEqual(result['state'],'draft')
            self.assertEqual(queue.get('bot@chatroom','101')['reply'],'你好呀～')
            self.assertIsNone(process_one(queue,Client(),{'bot@chatroom':'角色设定'}))
            box.close()

    def test_plugin_claim_prevents_ai_second_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            box=DurableInbox(Path(directory)/'q.sqlite','self',{'bot@chatroom'})
            try:
                box.ingest('s','bot@chatroom',[]);box.ingest('s','bot@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=1,text='echo hi')])
                queue=ReplyQueue(box)
                class Client:
                    def complete_json(self,*args):raise AssertionError('Claimed plugin source must not reach AI')
                result=process_one(queue,Client(),{'bot@chatroom':'prompt'},router=lambda message:True)
                self.assertEqual(result['state'],'plugin_claimed')
                self.assertEqual(queue.get('bot@chatroom','101')['state'],'ignored')
            finally:box.close()
