import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from linux_native.inbox import DurableInbox
from linux_native.replies import ReplyQueue
from linux_native.delivery import deliver


class DeliveryTests(unittest.TestCase):
    def test_waits_for_server_id_instead_of_rejecting_local_placeholder(self):
        with tempfile.TemporaryDirectory() as directory:
            box=DurableInbox(Path(directory)/'q.sqlite','self',{'bot@chatroom'})
            box.ingest('s','bot@chatroom',[])
            box.ingest('s','bot@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=1,text='hi')])
            queue=ReplyQueue(box);queue.stage(queue.claim(),'direct','hello')
            class Driver:
                commits=0
                def prepare(self,name,text):pass
                def commit(self):self.commits+=1
            driver=Driver()
            base=dict(chat_id='bot@chatroom',local_id=10,is_self=True,text='hello')
            observations=iter([[dict(base,server_id='0')],[dict(base,server_id='201')]])
            try:
                with patch('linux_native.delivery.time.sleep'):
                    self.assertTrue(deliver(queue,'bot@chatroom','101','bot',driver,lambda:9,lambda after:next(observations)))
                self.assertEqual(driver.commits,1)
                self.assertEqual(queue.get('bot@chatroom','101')['state'],'sent')
            finally:box.close()

    def test_cancel_during_prepare_never_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            box=DurableInbox(Path(directory)/'q.sqlite','self',{'bot@chatroom'})
            box.ingest('s','bot@chatroom',[])
            box.ingest('s','bot@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=1,text='hi')])
            queue=ReplyQueue(box);queue.stage(queue.claim(),'direct','hello')
            active=[True]
            class Driver:
                def prepare(self,name,text):active[0]=False
                def commit(self):raise AssertionError('Must not commit')
            with self.assertRaises(ValueError):deliver(queue,'bot@chatroom','101','bot',Driver(),lambda:9,lambda after:[],active=lambda:active[0])
            self.assertEqual(queue.get('bot@chatroom','101')['state'],'draft')
            box.close()

    def test_success_requires_new_matching_outgoing_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            box=DurableInbox(Path(directory)/'q.sqlite','self',{'bot@chatroom'})
            box.ingest('s','bot@chatroom',[])
            box.ingest('s','bot@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=1,text='hi')])
            queue=ReplyQueue(box);queue.stage(queue.claim(),'direct','hello')
            class Driver:
                committed=False
                def prepare(self,name,text):pass
                def commit(self):self.committed=True
            driver=Driver()
            proof=dict(chat_id='bot@chatroom',local_id=10,server_id='201',is_self=True,text='hello')
            self.assertTrue(deliver(queue,'bot@chatroom','101','bot',driver,lambda:9,lambda after:[proof]))
            self.assertTrue(driver.committed)
            self.assertEqual(queue.get('bot@chatroom','101')['state'],'sent')
            self.assertEqual(box.db.execute('SELECT COUNT(*) FROM delivery_events').fetchone()[0],1)
            box.close()

    def test_commit_failure_leaves_uncertain_and_never_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            box=DurableInbox(Path(directory)/'q.sqlite','self',{'bot@chatroom'})
            box.ingest('s','bot@chatroom',[])
            box.ingest('s','bot@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=1,text='hi')])
            queue=ReplyQueue(box);queue.stage(queue.claim(),'direct','hello')
            class Driver:
                def prepare(self,name,text):pass
                def commit(self):raise OSError('uncertain')
            with self.assertRaises(OSError):deliver(queue,'bot@chatroom','101','bot',Driver(),lambda:9,lambda after:[])
            self.assertEqual(queue.get('bot@chatroom','101')['state'],'uncertain')
            box.close()

    def test_preparation_failure_keeps_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            box=DurableInbox(Path(directory)/'q.sqlite','self',{'bot@chatroom'})
            box.ingest('s','bot@chatroom',[])
            box.ingest('s','bot@chatroom',[dict(local_id=1,server_id='101',sender_id='other',type=1,text='hi')])
            queue=ReplyQueue(box);queue.stage(queue.claim(),'direct','hello')
            class Driver:
                def prepare(self,name,text):raise ValueError('wrong group')
            with self.assertRaises(ValueError):deliver(queue,'bot@chatroom','101','bot',Driver(),lambda:9,lambda after:[])
            self.assertEqual(queue.get('bot@chatroom','101')['state'],'draft')
            box.close()
