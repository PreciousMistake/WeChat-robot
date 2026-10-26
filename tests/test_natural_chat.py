import unittest
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from wxbot_framework.natural_chat import Conversation, Decision


class NaturalChatTests(unittest.TestCase):
    def message(self, ident, text='你好', **extra):
        return dict(room='bot', session='s1', sender='a', text=text,
                    ident=ident, is_self=False, **extra)

    def test_scope_duplicates_and_self(self):
        c = Conversation('bot')
        self.assertFalse(c.ingest(**{**self.message('1'), 'room':'other'}, now=0))
        self.assertFalse(c.ingest(**{**self.message('2'), 'is_self':True}, now=0))
        self.assertTrue(c.ingest(**self.message('3'), now=0))
        self.assertFalse(c.ingest(**self.message('3'), now=1))

    def test_batching_and_context_bound(self):
        c = Conversation('bot')
        for i in range(15):
            c.ingest(**self.message(str(i)), now=i*.1)
        self.assertIsNone(c.take(2))
        job = c.take(5)
        self.assertEqual(len(job['messages']), 12)
        self.assertIsNone(c.take(6))

    def test_quote_survives_long_current_message_and_keeps_author(self):
        c = Conversation('bot')
        quote = dict(text='引用原文', author_id='original', author_name='原作者')
        c.ingest(**self.message('1', text='问'*800, quote=quote), now=0)
        entry = c.take(4)['messages'][0]
        self.assertEqual(len(entry['text']), 500)
        self.assertEqual(entry['speaker'], 'a')
        self.assertEqual(entry['quote']['text'], '引用原文')
        self.assertEqual(entry['quote']['author_id'], 'original')
        quote['text'] = '后续修改'
        self.assertEqual(entry['quote']['text'], '引用原文')

    def test_quote_context_is_bounded_and_drops_unknown_fields(self):
        c = Conversation('bot')
        c.ingest(**self.message('1', quote={'text':'原'*16000, 'extra':'secret'}), now=0)
        quote = c.take(4)['messages'][0]['quote']
        self.assertEqual(len(quote['text']), 500)
        self.assertNotIn('extra', quote)

    def test_session_switch_and_new_message_invalidate_job(self):
        c = Conversation('bot')
        c.ingest(**self.message('1'), now=0)
        job = c.take(4)
        self.assertTrue(c.current(job, 5))
        c.ingest(**self.message('2'), now=6)
        self.assertFalse(c.current(job, 7))
        c.ingest(**{**self.message('3'), 'session':'s2'}, now=8)
        self.assertEqual(len(c.messages), 1)

    def test_rolling_limits_direct_is_exempt(self):
        c = Conversation('bot', cooldown=30, hourly=2)
        self.assertTrue(c.allowed('participate', 0))
        c.sent('participate', '一', 0)
        self.assertFalse(c.allowed('participate', 29))
        self.assertTrue(c.allowed('direct', 29))
        self.assertTrue(c.allowed('participate', 30))
        c.sent('participate', '二', 30)
        self.assertFalse(c.allowed('participate', 90))
        self.assertTrue(c.allowed('participate', 3601))

    def test_decision_is_strict(self):
        self.assertEqual(Decision.parse({'decision':'ignore'}).reply, '')
        self.assertEqual(Decision.parse({'decision':'direct','reply':'好呀'}).reply, '好呀')
        for data in ({'decision':'send','reply':'x'}, {'decision':'direct','reply':''},
                     {'decision':'direct','reply':'x'*401}, {'decision':'direct','reply':42},
                     {'decision':'direct','reply':'hello\x00'}):
            with self.assertRaises(ValueError):
                Decision.parse(data)


spec = importlib.util.spec_from_file_location('natural_plugin', Path(__file__).parents[1]/'plugins/natural_chat/__init__.py')
plugin_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin_module)


class NaturalPluginTests(unittest.TestCase):
    def test_game_claim_invalidates_pending_ai_reply(self):
        def game_starts(*args):
            self.plugin.suppress_message(self.msg)
            return {'decision':'direct','reply':'不应额外回复'}
        self.ctx.llm.complete_json.side_effect=game_starts
        self.step()
        self.ctx.reply.assert_not_called()
        self.assertFalse(self.plugin.state.pending)

    def test_unchanged_room_keeps_current_job_when_another_room_is_added(self):
        state=self.plugin.state
        job=state.take(4)
        self.ctx.members.groups['new@chatroom']=[]
        self.plugin.apply_settings(self.ctx.config,self.ctx)
        self.assertIs(self.plugin.states['bot@chatroom'],state)
        self.assertTrue(state.current(job,5))
        self.assertIn('new@chatroom',self.plugin.states)

    def test_live_settings_keep_state_but_invalidate_changed_room_job(self):
        state=self.plugin.state
        state.sent('participate','之前说过',-30)
        job=state.take(4)
        options=dict(self.ctx.config,cooldown_seconds=10,hourly_limit=60)
        self.plugin.apply_settings(options,self.ctx)
        self.assertIs(self.plugin.state,state)
        self.assertEqual(state.cooldown,10)
        self.assertEqual(len(state.proactive),1)
        self.assertFalse(state.current(job,5))
        self.assertTrue(any(item['text']=='之前说过' for item in state.messages))

    def setUp(self):
        self.ctx = SimpleNamespace(config={'room':'bot@chatroom','dry_run':False},
            members=SimpleNamespace(groups={'bot@chatroom':[]}), logger=Mock(), llm=Mock(),
            plugin_is_active=lambda name: name=='natural_chat', reply=Mock(return_value=True))
        self.plugin = plugin_module.Plugin()
        with patch.object(plugin_module.threading, 'Thread'):
            self.plugin.on_load(self.ctx)
        self.msg = SimpleNamespace(type=1,is_group=True,is_self=False,text='机器人你好',
            chat_id='bot@chatroom',session_id='s1',sender_id='a',sequence=1,
            data={'native_message_id':'10'})
        with patch.object(plugin_module.time,'monotonic',return_value=0):
            self.plugin.on_message(self.msg,self.ctx)

    def step(self):
        with patch.object(plugin_module.time,'monotonic',return_value=4):
            self.plugin._step(self.ctx)

    def test_direct_delivers_once_and_remembers_reply(self):
        self.ctx.llm.complete_json.return_value={'decision':'direct','reply':'你好呀'}
        self.step()
        self.assertEqual(self.ctx.reply.call_args.args,(self.msg,'你好呀'))
        self.assertTrue(callable(self.ctx.reply.call_args.kwargs['delivery_guard']))
        self.step()
        self.assertEqual(self.ctx.reply.call_count,1)
        self.assertTrue(self.plugin.state.messages[-1]['bot'])

    def test_failed_send_does_not_consume_proactive_quota(self):
        self.ctx.llm.complete_json.return_value={'decision':'participate','reply':'我也这么想'}
        self.ctx.reply.return_value=False
        self.step()
        self.assertEqual(len(self.plugin.state.proactive),0)

    def test_new_input_during_model_call_discards_old_reply(self):
        def slow(*args):
            self.plugin.state.ingest(room='bot@chatroom',session='s1',sender='b',
                text='新消息',ident='11',is_self=False,now=3)
            return {'decision':'direct','reply':'过期'}
        self.ctx.llm.complete_json.side_effect=slow
        self.step()
        self.ctx.reply.assert_not_called()

    def test_model_failure_is_silent_in_group(self):
        self.ctx.llm.complete_json.side_effect=RuntimeError('provider error')
        self.step()
        self.ctx.reply.assert_not_called()

    def test_dry_run_writes_only_draft(self):
        import tempfile
        self.plugin.dry_run=True
        self.ctx.llm.complete_json.return_value={'decision':'direct','reply':'草稿'}
        with tempfile.TemporaryDirectory() as folder:
            self.plugin.drafts=Path(folder)/'draft.jsonl'
            self.step()
            self.assertIn('草稿',self.plugin.drafts.read_text(encoding='utf-8'))
        self.ctx.reply.assert_not_called()

    def test_stop_during_request_prevents_late_reply(self):
        def stop(*args):
            self.plugin.stop_event.set()
            return {'decision':'direct','reply':'迟到'}
        self.ctx.llm.complete_json.side_effect=stop
        self.step()
        self.ctx.reply.assert_not_called()

    def test_stop_during_sdk_pacing_prevents_dispatch(self):
        dispatched=[]
        def paced(message, text, *, delivery_guard):
            self.plugin.stop_event.set()
            with delivery_guard():
                dispatched.append(text)
        self.ctx.reply.side_effect=paced
        self.ctx.llm.complete_json.return_value={'decision':'direct','reply':'迟到'}
        self.step()
        self.assertEqual(dispatched,[])


    def test_new_input_is_not_blocked_by_sdk_pacing(self):
        import threading
        dispatched=[]
        ingested=threading.Event()
        newer=SimpleNamespace(**{**vars(self.msg),'sequence':2,'text':'新问题',
                                 'data':{'native_message_id':'11'}})
        def paced(message,text,*,delivery_guard):
            def receive():
                self.plugin.on_message(newer,self.ctx)
                ingested.set()
            thread=threading.Thread(target=receive,daemon=True)
            thread.start()
            self.assertTrue(ingested.wait(.5),'Incoming callback blocked during SDK pacing')
            with delivery_guard():
                dispatched.append(text)
            thread.join()
        self.ctx.reply.side_effect=paced
        self.ctx.llm.complete_json.return_value={'decision':'direct','reply':'旧回复'}
        self.step()
        self.assertTrue(ingested.is_set())
        self.assertEqual(dispatched,[])


class PersonaTests(unittest.TestCase):
    def test_override_source_uses_project_root_and_account_workspace(self):
        import tempfile
        project=Path(__file__).parents[1]
        with tempfile.TemporaryDirectory() as folder:
            ctx=SimpleNamespace(config={'room':'bot@chatroom','persona_file':'personas/whale.md'},
                project_root=project,workspace=Path(folder),
                members=SimpleNamespace(groups={'bot@chatroom':[]}),logger=Mock(),llm=Mock(),
                plugin_is_active=lambda name:False)
            plugin=plugin_module.Plugin()
            with patch.object(plugin_module,'__file__',str(Path(folder)/'versions/abc/__init__.py')), \
                    patch.object(plugin_module.threading,'Thread'):
                plugin.on_load(ctx)
            self.assertIn('鲸鱼娘',plugin.system)
            self.assertEqual(plugin.drafts,Path(folder)/'.runtime/natural-chat-drafts.jsonl')

    def test_prompt_keeps_persona_and_finishes_with_transport_constraints(self):
        prompt=plugin_module.build_system('你是鲸鱼娘，喜欢白米饭。')
        self.assertIn('喜欢白米饭',prompt)
        self.assertNotIn('不自称鲸鱼娘',prompt)
        self.assertGreater(prompt.rfind('输出约束'),prompt.find('喜欢白米饭'))

    def test_persona_loader_is_scoped_and_requires_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'whale.md').write_text('鲸鱼娘',encoding='utf-8')
            self.assertEqual(plugin_module.load_persona(root,'whale.md'),'鲸鱼娘')
            self.assertEqual(plugin_module.load_persona(root,None),'')
            for path in ('../other.md','missing.md'):
                with self.assertRaises((ValueError,FileNotFoundError)):
                    plugin_module.load_persona(root,path)


class MultiGroupTests(unittest.TestCase):
    def setUp(self):
        self.ctx=SimpleNamespace(config={'enabled':True,'dry_run':False,'personas':{
            'cat':{'name':'猫','text':'你是猫，喜欢晒太阳。'}},'group_settings':{
            'a@chatroom':{'persona_id':'neutral','cooldown_seconds':30,'hourly_limit':1},
            'b@chatroom':{'persona_id':'cat','cooldown_seconds':1,'hourly_limit':10}}},
            members=SimpleNamespace(groups={'a@chatroom':[],'b@chatroom':[]}),
            logger=Mock(),llm=Mock(),plugin_is_active=lambda n:n=='natural_chat',reply=Mock(return_value=True))
        self.plugin=plugin_module.Plugin()
        with patch.object(plugin_module.threading,'Thread'):
            self.plugin.on_load(self.ctx)

    def ingest(self,room,text,now,ident='same-id'):
        msg=SimpleNamespace(type=1,is_group=True,is_self=False,text=text,chat_id=room,
            session_id='s1',sender_id='user',sequence=1,data={'native_message_id':ident})
        with patch.object(plugin_module.time,'monotonic',return_value=now):
            self.plugin.on_message(msg,self.ctx)
        return msg

    def step(self,now):
        with patch.object(plugin_module.time,'monotonic',return_value=now):
            self.plugin._step(self.ctx)

    def test_same_ids_and_distinct_contexts_and_personas(self):
        a=self.ingest('a@chatroom','A群秘密',0)
        b=self.ingest('b@chatroom','B群秘密',1)
        self.ctx.llm.complete_json.return_value={'decision':'direct','reply':'收到'}
        self.step(4)
        first=self.ctx.llm.complete_json.call_args
        self.assertIn('A群秘密',first.args[1]); self.assertNotIn('B群秘密',first.args[1])
        self.assertNotIn('喜欢晒太阳',first.args[0])
        self.assertEqual(self.ctx.reply.call_args.args[0].chat_id,a.chat_id)
        self.step(10)
        second=self.ctx.llm.complete_json.call_args
        self.assertIn('B群秘密',second.args[1]); self.assertNotIn('A群秘密',second.args[1])
        self.assertIn('喜欢晒太阳',second.args[0])
        self.assertEqual(self.ctx.reply.call_args.args[0].chat_id,b.chat_id)

    def test_other_group_during_request_does_not_invalidate_first(self):
        self.ingest('a@chatroom','A提问',0)
        def model(*args):
            self.ingest('b@chatroom','B提问',4)
            return {'decision':'direct','reply':'A回复'}
        self.ctx.llm.complete_json.side_effect=model
        self.step(4)
        self.assertEqual(self.ctx.reply.call_args.args[0].chat_id,'a@chatroom')
        self.assertTrue(self.plugin.states['b@chatroom'].pending)

    def test_proactive_budget_is_per_group(self):
        self.ingest('a@chatroom','A',0)
        self.ingest('b@chatroom','B',1)
        self.ctx.llm.complete_json.return_value={'decision':'participate','reply':'接话'}
        self.step(4)
        self.step(10)
        self.assertEqual(self.ctx.reply.call_count,2)
        self.assertFalse(self.plugin.states['a@chatroom'].allowed('participate',11))
        self.assertTrue(self.plugin.states['b@chatroom'].allowed('participate',11))

    def test_unconfigured_group_is_ignored(self):
        self.ingest('c@chatroom','陌生群',0)
        self.step(4)
        self.ctx.llm.complete_json.assert_not_called()


if __name__ == '__main__':
    unittest.main()
