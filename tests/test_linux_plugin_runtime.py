import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


class FakeJobs:
    def __init__(self, *args): self.items=[]; self.waiting=False
    @staticmethod
    def plugin_version(state,name):
        config=json.loads((Path(state)/'plugin_config.json').read_text())
        return json.dumps(config.get('plugins',{}).get(name,{}),sort_keys=True)
    def enqueue(self,*args,**kwargs): self.items.append((args,kwargs));return 'job'
    def wait(self,jobid,*,valid,timeout): self.waiting=True;return valid()


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.state=Path(self.temp.name)
        self.options={'account':'wxid_self','enabled':True,'draft_only':False,'systems':{'123@chatroom':'friendly'},'group_settings':{}}
        self.write('ai_config.json',self.options)
        self.write('plugin_config.json',{'generation':1,'plugins':{}})
    def tearDown(self): self.doCleanups();self.temp.cleanup()
    def write(self,name,value): (self.state/name).write_text(json.dumps(value),encoding='utf-8')

    def test_defaults_are_all_off_and_managed_plugins_cannot_start(self):
        from linux_native.plugin_runtime import PluginRuntime
        with patch('linux_native.plugin_runtime.PluginJobs',FakeJobs):
            runtime=PluginRuntime(self.state,'wxid_self')
            runtime.refresh(self.options)
            self.assertFalse(runtime.manager.records)
            self.write('plugin_config.json',{'generation':2,'plugins':{'personality':{'enabled':True},'natural_chat':{'enabled':True}}})
            runtime.refresh(self.options)
            self.assertFalse(runtime.manager.records)
            runtime.close()

    def test_route_once_preserves_stable_source_and_scopes_groups(self):
        from linux_native.plugin_runtime import PluginRuntime
        with patch('linux_native.plugin_runtime.PluginJobs',FakeJobs):
            runtime=PluginRuntime(self.state,'wxid_self');runtime.refresh(self.options)
            seen=[]
            runtime.manager.dispatch=lambda message: seen.append(message) or {'claimed':True}
            payload={'chat_id':'123@chatroom','server_id':'987654321','sender_id':'wxid_user','text':'hello','type':1}
            self.assertTrue(runtime.route(payload));self.assertTrue(runtime.route(payload))
            self.assertEqual(len(seen),1)
            self.assertEqual(seen[0].sequence,987654321)
            self.assertEqual(seen[0].session_id,'linux:wxid_self')
            self.assertEqual(seen[0].data['room_wxid'],'123@chatroom')
            self.assertEqual(seen[0].data['msg'],'hello')
            self.assertFalse(runtime.route(dict(payload,chat_id='456@chatroom')))
            runtime.close()
            restarted=PluginRuntime(self.state,'wxid_self');restarted.refresh(self.options)
            restarted.manager.dispatch=lambda message:self.fail('Source routed twice across restart')
            self.assertTrue(restarted.route(payload))
            restarted.close()

    def test_service_waits_outside_guard_and_keeps_action_indices(self):
        from linux_native.plugin_service import PluginService
        from wxbot_framework.sdk import Message
        jobs=FakeJobs();service=PluginService(self.state,'wxid_self',jobs,lambda:True,lambda: self.options)
        self.write('plugin_config.json',{'plugins':{'echo':{'enabled':True}}})
        proxy=service.for_plugin('echo',{'enabled':True})
        guarded=[False]
        @contextmanager
        def guard():
            guarded[0]=True
            try: yield
            finally: guarded[0]=False
        jobs.wait=lambda *a,**k: self.assertFalse(guarded[0]) or True
        message=Message('hi','123@chatroom',sender_id='wxid_user',session_id='linux:wxid_self',sequence=9,is_group=True,data={'server_id':'9'})
        with proxy.handling(message):
            self.assertTrue(proxy.send_text(message.chat_id,'one',expected_session=message.session_id,delivery_guard=guard))
            self.assertTrue(proxy.send_text(message.chat_id,'two'))
        self.assertEqual([item[0][4] for item in jobs.items],[0,1])
        self.assertEqual(jobs.items[0][0][2],'echo')
        self.write('plugin_config.json',{'plugins':{'echo':{'enabled':False}}})
        with proxy.handling(message):
            with self.assertRaises(RuntimeError):proxy.send_text(message.chat_id,'stale')

    def test_crash_after_plugin_dispatch_does_not_repeat_opening(self):
        from linux_native.plugin_runtime import PluginRuntime
        with patch('linux_native.plugin_runtime.PluginJobs',FakeJobs):
            runtime=PluginRuntime(self.state,'wxid_self');runtime.refresh(self.options)
            def crash(message):raise RuntimeError('callback side effect before crash')
            runtime.manager.dispatch=crash
            payload={'chat_id':'123@chatroom','server_id':'900','sender_id':'wxid_user','text':'成语接龙','type':1}
            with self.assertRaises(RuntimeError):runtime.route(payload)
            runtime.close()
            runtime=PluginRuntime(self.state,'wxid_self');runtime.refresh(self.options)
            runtime.manager.dispatch=lambda message:self.fail('Ambiguous plugin source replayed')
            self.assertTrue(runtime.route(payload))
            runtime.close()

    def test_routes_real_group_media_and_recall_without_turning_xml_into_text(self):
        from linux_native.plugin_runtime import PluginRuntime
        with patch('linux_native.plugin_runtime.PluginJobs',FakeJobs):
            runtime=PluginRuntime(self.state,'wxid_self');self.addCleanup(runtime.close)
            runtime.refresh(self.options);seen=[]
            runtime.manager.dispatch=lambda message:seen.append(message) or {'claimed':True}
            payload={'chat_id':'123@chatroom','server_id':'901','sender_id':'wxid_user','text':'<sysmsg/>','type':10002,'recall':{'message_id':'900'}}
            self.assertTrue(runtime.route(payload))
            self.assertEqual(seen[0].type,10002)
            self.assertEqual(seen[0].text,'')
            self.assertEqual(seen[0].data['recall']['message_id'],'900')

    def test_service_rejects_cross_room_and_missing_message_context(self):
        from linux_native.plugin_service import PluginService
        from wxbot_framework.sdk import Message
        self.write('plugin_config.json',{'plugins':{'echo':{'enabled':True}}})
        proxy=PluginService(self.state,'wxid_self',FakeJobs(),lambda:True,lambda:self.options).for_plugin('echo',{'enabled':True})
        with self.assertRaises(RuntimeError):proxy.send_text('123@chatroom','hi')
        with proxy.handling(Message('hi','123@chatroom',session_id='linux:wxid_self',sequence=9)):
            with self.assertRaises(ValueError):proxy.send_text('456@chatroom','hi')

    def test_accepts_respects_empty_group_scope_and_live_stop(self):
        from linux_native.plugin_service import PluginService
        from wxbot_framework.sdk import Message
        message=Message('hi','123@chatroom',is_group=True,session_id='linux:wxid_self',sequence=9)
        self.write('plugin_config.json',{'plugins':{'echo':{'enabled':True,'groups':[]}}})
        service=PluginService(self.state,'wxid_self',FakeJobs(),lambda:True,lambda:self.options)
        self.assertFalse(service.for_plugin('echo',{}).accepts(message))
        self.write('plugin_config.json',{'plugins':{'echo':{'enabled':True}}})
        proxy=service.for_plugin('echo',{})
        self.assertTrue(proxy.accepts(message))
        self.write('ai_config.json',dict(self.options,enabled=False))
        self.assertFalse(proxy.accepts(message))

    def test_guard_rejects_member_clear_and_changed_group_persona(self):
        from linux_native.plugin_service import member_guards,persistent_guards_valid,system_hash
        from wxbot_framework.members import MemberStore
        self.write('plugin_members.json',{'groups':{'123@chatroom':[]},'database_file':'.runtime/plugin_members.sqlite3','history_enabled':True})
        members=MemberStore(self.state/'plugin_members.json','wxid_self');members.initialize()
        guards=dict(member_guards(members.path,'wxid_self','123@chatroom'),account='wxid_self',session='linux:wxid_self',systems_hash=system_hash('friendly'))
        self.assertTrue(persistent_guards_valid(self.state,'wxid_self','123@chatroom',guards))
        members.clear('123@chatroom')
        self.assertFalse(persistent_guards_valid(self.state,'wxid_self','123@chatroom',guards))
        self.write('ai_config.json',dict(self.options,systems={'123@chatroom':'different'}))
        self.assertFalse(persistent_guards_valid(self.state,'wxid_self','123@chatroom',guards))
        members.close()

    def test_original_echo_and_legacy_callbacks_create_outbox_actions(self):
        from linux_native.plugin_runtime import PluginRuntime
        self.write('capabilities.json',{'capabilities':['text.receive','text.send','group.text.receive','group.text.send']})
        self.write('plugin_config.json',{'plugins':{'echo':{'enabled':True},'id_responder':{'enabled':True}}})
        runtime=PluginRuntime(self.state,'wxid_self');self.addCleanup(runtime.close)
        runtime.jobs.wait=lambda *args,**kwargs:True
        runtime.refresh(self.options)
        for source,text in [('1001','echo hello'),('1002','test')]:
            self.assertTrue(runtime.route({'chat_id':'123@chatroom','server_id':source,'sender_id':'wxid_user','text':text,'type':1}))
        for record in runtime.manager.records.values():record.queue.join()
        with runtime.jobs.connection() as db:
            actions=db.execute('SELECT plugin,payload FROM jobs ORDER BY rowid').fetchall()
        self.assertEqual(len(actions),2)
        by_name={name:json.loads(payload)['text'] for name,payload in actions}
        self.assertEqual(by_name,{'echo':'hello','id_responder':'123@chatroom'})

    def test_true_member_roles_survive_display_name_overlay_and_account_checks(self):
        from linux_native.plugin_service import PluginService
        from types import SimpleNamespace
        self.write('plugin_config.json',{'plugins':{'echo':{'enabled':True}}})
        self.write('group_members.json',{'account':'wxid_self','groups':{'123@chatroom':[{'wxid':'wxid_owner','nickname':'native','alias':'alias','role':'owner'}]}})
        members=SimpleNamespace(find_by_wxid=lambda wxid,room:SimpleNamespace(self_named=True,display_name='chosen'))
        proxy=PluginService(self.state,'wxid_self',FakeJobs(),lambda:True,lambda:self.options,members=members).for_plugin('echo',{})
        self.assertEqual(proxy.group_members('123@chatroom')[0],{'wxid':'wxid_owner','nickname':'chosen','alias':'alias','role':'owner'})
        self.assertEqual(proxy.group_member_role('123@chatroom','wxid_owner'),'owner')
        self.assertEqual(proxy.group_member_role('123@chatroom','wxid_other'),'unknown')
        self.write('group_members.json',{'account':'wxid_other','groups':{'123@chatroom':[]}})
        with self.assertRaises(ValueError):proxy.group_members('123@chatroom')

    def test_warm_original_only_enqueues_recall_plugins_and_preserves_attachment(self):
        from linux_native.plugin_runtime import PluginRuntime
        from types import SimpleNamespace
        with patch('linux_native.plugin_runtime.PluginJobs',FakeJobs):
            runtime=PluginRuntime(self.state,'wxid_self');self.addCleanup(runtime.close)
            runtime.refresh(self.options);seen=[]
            def record(name,accepted=True):
                return SimpleNamespace(active=True,context=SimpleNamespace(_service=SimpleNamespace(accepts=lambda message:accepted)),
                    spec=SimpleNamespace(message_types={49}),enqueue=lambda message:seen.append((name,message)) or True)
            runtime.manager.records={'anti_recall':record('anti_recall'),'anti_recall_file':record('anti_recall_file'),
                'echo':record('echo'),'anti_recall_image':record('anti_recall_image',False)}
            payload={'chat_id':'123@chatroom','server_id':'902','sender_id':'wxid_user','type':49,'file_path':'/tmp/a.pdf','file_status':'ready','file_name':'a.pdf'}
            self.assertEqual(runtime.warm_original(payload),2)
            self.assertEqual([name for name,message in seen],['anti_recall','anti_recall_file'])
            self.assertEqual(seen[0][1].file_path,'/tmp/a.pdf')
            self.assertEqual(seen[0][1].data['file_status'],'ready')
            self.assertEqual(runtime.routes.execute('SELECT COUNT(*) FROM routes').fetchone()[0],0)
            runtime.manager.records={}

    def test_business_models_do_not_receive_natural_chat_decision_protocol(self):
        from linux_native.plugin_runtime import plugin_persona
        self.write('admin_config.json',{'personas':{'whale':{'name':'whale','text':'role style only'}}})
        options=dict(self.options,group_settings={'123@chatroom':{'persona_id':'whale'}})
        self.assertTrue(plugin_persona(self.state,options,'123@chatroom').startswith('role style only'));self.assertNotIn('direct|participate',plugin_persona(self.state,options,'123@chatroom'))
        options['group_settings']['123@chatroom']['persona_id']='neutral'
        self.assertIn('未配置',plugin_persona(self.state,options,'123@chatroom'))


if __name__=='__main__':unittest.main()
