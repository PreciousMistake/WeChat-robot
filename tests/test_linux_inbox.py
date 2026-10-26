import tempfile
import unittest
from pathlib import Path

from linux_native.inbox import DurableInbox


class LinuxInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'inbox.sqlite'
        self.box=DurableInbox(self.path,'self',{'bot@chatroom'})

    def tearDown(self):
        self.box.close();self.temp.cleanup()

    def row(self,local,server,sender='other',text='你好'):
        return {'local_id':local,'server_id':str(server),'sender_id':sender,'type':1,'text':text}

    def test_bootstrap_skips_history_and_next_poll_adds_new_only(self):
        self.assertEqual(self.box.ingest('message_0','bot@chatroom',[self.row(1,101)]),0)
        self.assertEqual(self.box.ingest('message_0','bot@chatroom',[self.row(1,101),self.row(2,102)]),1)
        self.assertEqual(self.box.pending()[0]['text'],'你好')

    def test_cursor_and_inbox_survive_restart_and_duplicate_server_ids(self):
        self.box.ingest('message_0','bot@chatroom',[])
        self.box.ingest('message_0','bot@chatroom',[self.row(1,101)])
        self.box.close();self.box=DurableInbox(self.path,'self',{'bot@chatroom'})
        self.assertEqual(self.box.ingest('message_0','bot@chatroom',[self.row(1,101),self.row(2,101)]),0)
        self.assertEqual(len(self.box.pending()),1)
        self.assertEqual(self.box.cursor('message_0','bot@chatroom'),2)

    def test_self_and_unallowed_groups_and_invalid_ids_do_not_enqueue(self):
        self.box.ingest('message_0','bot@chatroom',[])
        self.assertEqual(self.box.ingest('message_0','bot@chatroom',[self.row(1,101,'self'),self.row(2,0),self.row(3,103,'')]),0)
        with self.assertRaises(ValueError):self.box.ingest('message_0','other@chatroom',[self.row(1,101)])
        self.assertEqual(self.box.pending(),[])

    def test_bad_batch_rolls_back_cursor_and_inserts(self):
        self.box.ingest('message_0','bot@chatroom',[])
        with self.assertRaises(ValueError):
            self.box.ingest('message_0','bot@chatroom',[self.row(1,101),dict(self.row(2,102),local_id=-1)])
        self.assertEqual(self.box.pending(),[])
        self.assertEqual(self.box.cursor('message_0','bot@chatroom'),0)

    def test_account_state_cannot_be_reused_for_another_account(self):
        with self.assertRaises(ValueError):DurableInbox(self.path,'other-self',{'bot@chatroom'})


if __name__=='__main__':unittest.main()
