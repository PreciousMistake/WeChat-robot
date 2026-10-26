import tempfile,unittest,sqlite3
from pathlib import Path
from linux_native.receiver_plugins import PluginObservations
from linux_native.worker import table_name

class ObservationIntegrationTests(unittest.TestCase):
 def test_recall_system_row_without_sender_uses_cached_original_author(self):
  with tempfile.TemporaryDirectory() as folder:
   state=Path(folder);o=PluginObservations(state,'wxid_self',state);db=sqlite3.connect(':memory:');room='123@chatroom';t=table_name(room)
   db.executescript('CREATE TABLE Name2Id(user_name TEXT);INSERT INTO Name2Id VALUES("wxid_other");CREATE TABLE '+t+'(local_id INTEGER,server_id INTEGER,real_sender_id INTEGER,local_type INTEGER,message_content TEXT,compress_content BLOB,create_time INTEGER);')
   try:
    db.execute('INSERT INTO '+t+' VALUES(1,101,1,1,"hello",NULL,100)');o.observe(db,'s',room)
    db.execute('UPDATE '+t+' SET real_sender_id=99,local_type=10000,message_content=?',('<sysmsg type="revokemsg"><revokemsg><content>notice</content></revokemsg></sysmsg>',))
    o.observe(db,'s',room);events=o.events.pending()
    self.assertEqual(len(events),1);self.assertEqual(events[0]['payload']['recall']['message_id'],'101');self.assertEqual(events[0]['payload']['sender_id'],'wxid_other')
   finally:db.close();o.close()
