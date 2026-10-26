import json,tempfile,unittest
from pathlib import Path
from linux_native.plugin_admin import PluginAdmin

class PluginAdminTests(unittest.TestCase):
 def test_catalog_preserves_all_plugins_but_only_verified_capabilities_enable(self):
  with tempfile.TemporaryDirectory() as folder:
   state=Path(folder);(state/'ai_config.json').write_text(json.dumps({'account':'wxid_self','enabled':True,'systems':{'123@chatroom':'x'}}));(state/'capabilities.json').write_text(json.dumps({'capabilities':['text.receive','text.send','group.text.receive','group.text.send','voice.send']}))
   admin=PluginAdmin(state,Path(__file__).resolve().parents[1]);rows=admin.catalog()['plugins']
   self.assertEqual(len(rows),17);self.assertFalse(next(p for p in rows if p['name']=='echo')['enabled'])
   admin.action({'name':'echo','action':'enable'})
   self.assertTrue(next(p for p in admin.catalog()['plugins'] if p['name']=='echo')['enabled'])
   with self.assertRaises(ValueError):admin.action({'name':'idiom_chain','action':'enable'})
   with self.assertRaises(ValueError):admin.action({'name':'natural_chat','action':'enable'})
 def test_business_editor_rejects_stale_versions_and_never_returns_files(self):
  with tempfile.TemporaryDirectory() as folder:
   state=Path(folder);(state/'ai_config.json').write_text(json.dumps({'account':'wxid_self','enabled':True,'systems':{}}))
   admin=PluginAdmin(state,Path(__file__).resolve().parents[1]);view=admin.business('echo')
   self.assertNotIn('files',view);view['options']['enabled']=False;view['options']['prefix']='repeat'
   result=admin.save_business(dict(view,action='save'))
   with self.assertRaises(Exception):admin.save_business(dict(view,action='save'))
   self.assertNotIn('files',result)
