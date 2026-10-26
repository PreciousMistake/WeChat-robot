import hashlib
import sqlite3
import unittest

from linux_native.worker import read_rows,ingest_database
from linux_native.inbox import DurableInbox
import tempfile
from pathlib import Path


class LinuxReaderTests(unittest.TestCase):
    def test_newly_enabled_group_reads_only_messages_after_activation(self):
        db=sqlite3.connect(':memory:');room='bot@chatroom';table='Msg_'+hashlib.md5(room.encode()).hexdigest()
        db.executescript('CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES("other"); CREATE TABLE '+table+
            '(local_id INTEGER,server_id INTEGER,real_sender_id INTEGER,local_type INTEGER,message_content TEXT,compress_content BLOB,create_time INTEGER);')
        db.executemany('INSERT INTO '+table+' VALUES(?,?,1,1,?,NULL,?)',[(1,101,'old',100),(2,102,'new',200)])
        with tempfile.TemporaryDirectory() as directory:
            box=DurableInbox(Path(directory)/'inbox.sqlite','self',{room});box.finish_bootstrap()
            try:
                ingest_database(db,box,'new-group',room,min_create_time=150)
                self.assertEqual([p['text'] for p in box.pending()],['new'])
            finally:box.close()
        db.close()
    def test_group_table_sender_resolution_and_cursor(self):
        db=sqlite3.connect(':memory:')
        room='bot@chatroom';table='Msg_'+hashlib.md5(room.encode()).hexdigest()
        db.executescript('CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES("self"),("other");'+
            'CREATE TABLE '+table+'(local_id INTEGER,server_id INTEGER,real_sender_id INTEGER,local_type INTEGER,message_content TEXT,compress_content BLOB);')
        db.executemany('INSERT INTO '+table+' VALUES(?,?,?,?,?,?)',[(1,101,1,1,'old',None),(2,102,2,1,'other:\n你好',None)])
        rows=read_rows(db,room,1)
        self.assertEqual(rows,[{'local_id':2,'server_id':'102','sender_id':'other','type':1,'text':'你好'}])
        self.assertEqual(read_rows(db,'unrelated@chatroom',0),None)
        db.close()

    def test_missing_sender_is_not_inferred_from_text(self):
        db=sqlite3.connect(':memory:');room='bot@chatroom';table='Msg_'+hashlib.md5(room.encode()).hexdigest()
        db.executescript('CREATE TABLE Name2Id(user_name TEXT); CREATE TABLE '+table+
            '(local_id INTEGER,server_id INTEGER,real_sender_id INTEGER,local_type INTEGER,message_content TEXT,compress_content BLOB);')
        db.execute('INSERT INTO '+table+' VALUES(1,101,99,1,?,NULL)',('self:\n伪造文本',))
        self.assertEqual(read_rows(db,room,0)[0]['sender_id'],'')
        db.close()

    def test_corrupt_text_is_quarantined_without_blocking_next_row(self):
        db=sqlite3.connect(':memory:');room='bot@chatroom';table='Msg_'+hashlib.md5(room.encode()).hexdigest()
        db.executescript('CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES("other"); CREATE TABLE '+table+
            '(local_id INTEGER,server_id INTEGER,real_sender_id INTEGER,local_type INTEGER,message_content BLOB,compress_content BLOB);')
        db.executemany('INSERT INTO '+table+' VALUES(?,?,1,1,?,NULL)',[(1,101,b'\xff'),(2,102,b'valid')])
        db.execute('INSERT INTO '+table+" VALUES(3,103,1,1,CAST(x'ff' AS TEXT),NULL)")
        db.execute('INSERT INTO '+table+" VALUES(4,104,1,1,'after corrupt text',NULL)")
        rows=read_rows(db,room,0)
        self.assertEqual(rows[0]['decode_error'],'invalid_text')
        with tempfile.TemporaryDirectory() as directory:
            box=DurableInbox(Path(directory)/'inbox.sqlite','self',{room})
            box.ingest('stream',room,[]);box.ingest('stream',room,rows)
            self.assertEqual([p['text'] for p in box.pending()],['valid','after corrupt text'])
            self.assertEqual(box.stats()['quarantined'],2)
            self.assertEqual(box.cursor('stream',room),4)
            box.close()
        db.close()

    def test_absent_group_table_and_new_shard_do_not_baseline_live_messages(self):
        db=sqlite3.connect(':memory:');room='bot@chatroom';table='Msg_'+hashlib.md5(room.encode()).hexdigest()
        db.execute('CREATE TABLE Name2Id(user_name TEXT)');db.execute('INSERT INTO Name2Id VALUES("other")')
        with tempfile.TemporaryDirectory() as directory:
            box=DurableInbox(Path(directory)/'inbox.sqlite','self',{room})
            ingest_database(db,box,'initial',room);box.finish_bootstrap()
            db.execute('CREATE TABLE '+table+'(local_id INTEGER,server_id INTEGER,real_sender_id INTEGER,local_type INTEGER,message_content TEXT,compress_content BLOB)')
            db.execute('INSERT INTO '+table+' VALUES(1,101,1,1,"new",NULL)')
            ingest_database(db,box,'initial',room)
            db.execute('INSERT INTO '+table+' VALUES(2,102,1,1,"shard",NULL)')
            ingest_database(db,box,'new-shard',room)
            self.assertEqual({p['server_id'] for p in box.pending()},{'101','102'})
            box.close()
        db.close()

    def test_voice_duration_is_metadata_without_exposing_xml(self):
        from linux_native.worker import voice_duration
        self.assertEqual(voice_duration('<msg><voicemsg voicelength="5100" length="12345" /></msg>'),5100)
        self.assertEqual(voice_duration('<msg/>'),0)

    def test_native_attachment_high_subtype_is_normalized(self):
        from linux_native.worker import table_name
        db=sqlite3.connect(':memory:');room='123@chatroom'
        db.execute('CREATE TABLE Name2Id(user_name TEXT)');db.execute('INSERT INTO Name2Id VALUES("other")')
        db.execute('CREATE TABLE '+table_name(room)+'(local_id INTEGER,server_id INTEGER,real_sender_id INTEGER,local_type INTEGER,message_content TEXT,compress_content BLOB,create_time INTEGER)')
        db.execute('INSERT INTO '+table_name(room)+' VALUES(1,101,1,?, ?,NULL,100)',((6<<32)|49,'<msg><appmsg><type>6</type></appmsg></msg>'))
        try:
            row=read_rows(db,room,0,include_metadata=True)[0]
            self.assertEqual(row['type'],49);self.assertEqual(row['app_type'],6);self.assertIn('<appmsg>',row['raw_content']);self.assertEqual(row['create_time'],100)
        finally:db.close()

    def test_outgoing_image_md5_is_available_for_exact_receipt_matching(self):
        from linux_native.worker import table_name
        db=sqlite3.connect(':memory:');room='123@chatroom';t=table_name(room)
        db.executescript('CREATE TABLE Name2Id(user_name TEXT);INSERT INTO Name2Id VALUES("self");CREATE TABLE '+t+'(local_id INTEGER,server_id INTEGER,real_sender_id INTEGER,local_type INTEGER,message_content TEXT,compress_content BLOB);')
        try:
            db.execute('INSERT INTO '+t+' VALUES(1,101,1,3,?,NULL)',('<msg><img md5="0123456789abcdef0123456789abcdef" /></msg>',))
            row=read_rows(db,room,0,include_metadata=True)[0]
            self.assertEqual(row['image_md5'],'0123456789abcdef0123456789abcdef')
        finally:db.close()

    def test_mentions_are_read_from_native_source_not_message_body(self):
        from linux_native.worker import table_name
        db=sqlite3.connect(':memory:');room='123@chatroom';t=table_name(room)
        db.executescript('CREATE TABLE Name2Id(user_name TEXT);INSERT INTO Name2Id VALUES("self");CREATE TABLE '+t+'(local_id INTEGER,server_id INTEGER,real_sender_id INTEGER,local_type INTEGER,message_content TEXT,compress_content BLOB,source BLOB);')
        try:
            db.execute('INSERT INTO '+t+' VALUES(1,101,1,1,?,NULL,?)',('@user hello',b'<msgsource><atuserlist>wxid_user</atuserlist><alnode><fr>1</fr></alnode></msgsource>'))
            self.assertEqual(read_rows(db,room,0)[0]['at_user_ids'],['wxid_user'])
            db.execute('UPDATE '+t+' SET source=NULL')
            self.assertNotIn('at_user_ids',read_rows(db,room,0)[0])
        finally:db.close()
