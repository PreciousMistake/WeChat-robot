import unittest,sqlite3
from pathlib import Path
from linux_native.dispatch import select_room_shard

class ShardTests(unittest.TestCase):
 def test_unrelated_shards_do_not_block_target(self):
  def snapshot(path,key):
   db=sqlite3.connect(':memory:')
   if key==b'target':db.execute('CREATE TABLE Msg_target(local_id INTEGER)')
   return db,None
  self.assertEqual(select_room_shard(Path('/db'),{'a':b'old','b':b'target'},'Msg_target',snapshot),('b',b'target'))
 def test_duplicate_room_tables_are_ambiguous(self):
  def snapshot(path,key):
   db=sqlite3.connect(':memory:');db.execute('CREATE TABLE Msg_target(local_id INTEGER)');return db,None
  with self.assertRaises(ValueError):select_room_shard(Path('/db'),{'a':b'a','b':b'b'},'Msg_target',snapshot)
