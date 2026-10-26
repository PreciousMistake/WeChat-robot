import json,tempfile,unittest
from pathlib import Path
from linux_native.plugin_jobs import PluginJobs

class PluginJobTests(unittest.TestCase):
 def test_old_uncertain_image_cannot_steal_a_newer_confirmed_receipt(self):
  import hashlib
  from types import SimpleNamespace
  from unittest.mock import patch
  from linux_native.plugin_dispatch import reconcile_media
  with tempfile.TemporaryDirectory() as folder:
   state=Path(folder);(state/'ai_config.json').write_text(json.dumps({'account':'wxid_self','enabled':True,'systems':{'123@chatroom':'x'}}));(state/'plugin_config.json').write_text(json.dumps({'plugins':{'image_gallery':{'enabled':True}}}))
   source=state/'source.png';source.write_bytes(b'repeated-image');q=PluginJobs(state,'wxid_self');version=q.plugin_version(state,'image_gallery')
   old=q.enqueue('123@chatroom','1','image_gallery',version,0,'image',{'path':str(source)});q.begin(old,10);q.fail(old)
   new=q.enqueue('123@chatroom','2','image_gallery',version,0,'image',{'path':str(source)});q.begin(new,20)
   proof=dict(chat_id='123@chatroom',is_self=True,sender_id='wxid_self',type=3,image_md5=hashlib.md5(source.read_bytes()).hexdigest(),local_id=21,server_id='30')
   q.confirm(new,proof)
   with self.assertRaisesRegex(ValueError,'already used'):q.confirm(old,proof)
   box=SimpleNamespace(groups={'123@chatroom'},account='wxid_self')
   with patch('linux_native.plugin_dispatch.select_room_shard',return_value=('db',b'key')),patch('linux_native.worker.snapshot',return_value=(SimpleNamespace(close=lambda:None),None)),patch('linux_native.worker.read_rows',return_value=[proof]):
    self.assertIsNone(reconcile_media(q,box,state,{}))
   self.assertEqual(q.get(old)['state'],'uncertain');self.assertEqual(q.get(new)['outgoing_id'],'30')
 def test_actions_are_deduplicated_and_disable_invalidates(self):
  with tempfile.TemporaryDirectory() as folder:
   state=Path(folder);(state/'ai_config.json').write_text(json.dumps({'account':'wxid_self','enabled':True,'systems':{'123@chatroom':'x'}}));(state/'plugin_config.json').write_text(json.dumps({'plugins':{'echo':{'enabled':True}}}))
   q=PluginJobs(state,'wxid_self');v=q.plugin_version(state,'echo')
   a=q.enqueue('123@chatroom','1','echo',v,0,'text',{'text':'hello'})
   self.assertEqual(q.enqueue('123@chatroom','1','echo',v,0,'text',{'text':'hello'}),a)
   with self.assertRaises(ValueError):q.enqueue('456@chatroom','1','echo',v,1,'text',{'text':'bad'})
   (state/'plugin_config.json').write_text(json.dumps({'plugins':{'echo':{'enabled':False}}}))
   self.assertFalse(q.valid(q.get(a)))
   self.assertFalse(q.wait(a,valid=lambda:False,timeout=.01))
 def test_sending_is_never_claimed_twice_and_receipt_is_exact(self):
  with tempfile.TemporaryDirectory() as folder:
   state=Path(folder);(state/'ai_config.json').write_text(json.dumps({'account':'wxid_self','enabled':True,'systems':{'123@chatroom':'x'}}));(state/'plugin_config.json').write_text(json.dumps({'plugins':{'echo':{'enabled':True}}}))
   q=PluginJobs(state,'wxid_self');v=q.plugin_version(state,'echo');a=q.enqueue('123@chatroom','1','echo',v,0,'text',{'text':'hello'})
   q.begin(a,10)
   with self.assertRaises(ValueError):q.begin(a,10)
   with self.assertRaises(ValueError):q.confirm(a,dict(chat_id='456@chatroom',is_self=True,type=1,text='hello',local_id=11,server_id='20'))
   q.confirm(a,dict(chat_id='123@chatroom',is_self=True,type=1,text='hello',local_id=11,server_id='20'))
   self.assertTrue(q.wait(a,valid=lambda:True,timeout=.01))

 def test_image_receipt_requires_original_md5_and_preserves_source(self):
  import hashlib
  with tempfile.TemporaryDirectory() as folder:
   state=Path(folder);(state/'ai_config.json').write_text(json.dumps({'account':'wxid_self','enabled':True,'systems':{'123@chatroom':'x'}}));(state/'plugin_config.json').write_text(json.dumps({'plugins':{'image_gallery':{'enabled':True}}}))
   source=state/'source.png';source.write_bytes(b'image-fixture');md5=hashlib.md5(source.read_bytes()).hexdigest()
   q=PluginJobs(state,'wxid_self');a=q.enqueue('123@chatroom','1','image_gallery',q.plugin_version(state,'image_gallery'),0,'image',{'path':str(source)})
   q.begin(a,10)
   with self.assertRaises(ValueError):q.confirm(a,dict(chat_id='123@chatroom',is_self=True,type=3,image_md5='0'*32,local_id=11,server_id='20'))
   q.confirm(a,dict(chat_id='123@chatroom',is_self=True,type=3,image_md5=md5,local_id=11,server_id='20'))
   self.assertTrue(source.exists());self.assertTrue(q.wait(a,valid=lambda:True,timeout=.01))

 def test_legacy_image_job_is_upgraded_only_from_hash_verified_spool(self):
  with tempfile.TemporaryDirectory() as folder:
   state=Path(folder);(state/'ai_config.json').write_text(json.dumps({'account':'wxid_self','enabled':True,'systems':{'123@chatroom':'x'}}));(state/'plugin_config.json').write_text(json.dumps({'plugins':{'image_gallery':{'enabled':True}}}))
   source=state/'source.png';source.write_bytes(b'image-fixture');q=PluginJobs(state,'wxid_self');a=q.enqueue('123@chatroom','1','image_gallery',q.plugin_version(state,'image_gallery'),0,'image',{'path':str(source)})
   legacy=q.get(a)['payload'];legacy.pop('md5');legacy.pop('size')
   with q.connection() as db:db.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(legacy),a))
   upgraded=q.get(a)['payload'];self.assertIn('md5',upgraded)
   with q.connection() as db:db.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(legacy),a))
   Path(legacy['path']).write_bytes(b'tampered')
   self.assertNotIn('md5',q.get(a)['payload'])

 def test_mention_receipt_requires_real_native_target_ids(self):
  with tempfile.TemporaryDirectory() as folder:
   state=Path(folder);(state/'ai_config.json').write_text(json.dumps({'account':'wxid_self','enabled':True,'systems':{'123@chatroom':'x'}}));(state/'plugin_config.json').write_text(json.dumps({'plugins':{'idiom_chain':{'enabled':True}}}))
   q=PluginJobs(state,'wxid_self');a=q.enqueue('123@chatroom','1','idiom_chain',q.plugin_version(state,'idiom_chain'),0,'at_text',{'text':'hello','mentions':['wxid_a']})
   q.set_prepared_text(a,'@Alpha hello');q.begin(a,10)
   proof=dict(chat_id='123@chatroom',is_self=True,type=1,text='@Alpha hello',local_id=11,server_id='20')
   with self.assertRaises(ValueError):q.confirm(a,proof)
   with self.assertRaises(ValueError):q.confirm(a,dict(proof,at_user_ids=['wxid_b']))
   q.confirm(a,dict(proof,at_user_ids=['wxid_a']));self.assertTrue(q.wait(a,timeout=.01))

 def test_native_mention_receipt_tolerates_client_trimming_only_outer_spaces(self):
  with tempfile.TemporaryDirectory() as folder:
   state=Path(folder);(state/'ai_config.json').write_text(json.dumps({'account':'wxid_self','enabled':True,'systems':{'123@chatroom':'x'}}));(state/'plugin_config.json').write_text(json.dumps({'plugins':{'idiom_chain':{'enabled':True}}}))
   q=PluginJobs(state,'wxid_self');a=q.enqueue('123@chatroom','1','idiom_chain',q.plugin_version(state,'idiom_chain'),0,'at_text',{'text':'hello','mentions':['wxid_a']})
   q.set_prepared_text(a,' @Alpha hello');q.begin(a,10)
   q.confirm(a,dict(chat_id='123@chatroom',is_self=True,type=1,text='@Alpha hello',at_user_ids=['wxid_a'],local_id=11,server_id='20'))
   self.assertTrue(q.wait(a,timeout=.01))
