import tempfile
import unittest
from pathlib import Path
from wxbot_framework.natural_settings import normalize_settings
from tests.test_web_panel import PanelFixture

class OwnerParity(unittest.TestCase):
    def test_owner_defaults_and_identity_fields_survive_normalization(self):
        row=normalize_settings({},['1@chatroom'],Path('.'))['group_settings']['1@chatroom']
        self.assertEqual(row.get('owner_wxid'),'')
        options={'group_settings':{'1@chatroom':{'owner_wxid':'wxid_real','owner_name':'名字'}}}
        self.assertEqual(normalize_settings(options,['1@chatroom'],Path('.'))['group_settings']['1@chatroom']['owner_wxid'],'wxid_real')

class ImagePanelParity(PanelFixture):
    def test_image_config_masks_and_retains_secret(self):
        self.controller.save_configuration({'image':{'enabled':True,'base_url':'https://example.com/v1','model':'test','api_key':'test-secret'}})
        image=self.controller.configuration()['image']
        self.assertTrue(image['api_key_configured'])
        self.assertNotIn('test-secret',str(image))
        self.controller.save_configuration({'image':{'size':'512x512','api_key':''}})
        import json
        saved=json.loads((self.root/'image_config.json').read_text())
        self.assertEqual(saved['api_key'],'test-secret')
        self.assertEqual(saved['size'],'512x512')
    def test_owner_cannot_be_bound_from_unverified_config(self):
        self.controller.save_configuration({'groups':['1@chatroom']})
        with self.assertRaises(ValueError):
            self.controller.save_configuration({'natural_chat':{'group_settings':{'1@chatroom':{'owner_wxid':'wxid_spoof','owner_name':'伪造'}}}})


class ImageWorkerParity(unittest.TestCase):
    def test_generation_runs_once_and_config_change_cancels_delivery(self):
        from wxbot_framework.image_generation import ImageGeneration
        import json
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            root.joinpath('image_config.json').write_text(json.dumps({'enabled':True,'base_url':'https://example.com','model':'x','api_key':'test','cooldown_seconds':0}))
            delivered=[];calls=[]
            class Client:
                def __init__(self, config):pass
                def generate(self,prompt,*,current):
                    calls.append(prompt)
                    return b'image'
            worker=ImageGeneration(root, client_factory=Client)
            kwargs=dict(room='1@chatroom',source='1',prompt='小猫',current=lambda:True,deliver=lambda path,guard:delivered.append(path.read_bytes()))
            self.assertTrue(worker.submit(**kwargs))
            self.assertFalse(worker.submit(**kwargs))
            worker.step()
            self.assertEqual(delivered,[b'image'])
            self.assertEqual(calls,['小猫'])
            kwargs['source']='2'
            self.assertTrue(worker.submit(**kwargs))
            root.joinpath('image_config.json').write_text('{}')
            worker.step()
            self.assertEqual(calls,['小猫'])
            self.assertEqual(delivered,[b'image'])

from tests import test_natural_chat as natural_tests
class NaturalOwnershipParity(unittest.TestCase):
    setUp = natural_tests.NaturalPluginTests.setUp
    step = natural_tests.NaturalPluginTests.step
    def test_speaker_identity_uses_real_id_and_blocks_nickname_claim(self):
        self.plugin.group_settings['bot@chatroom'].update(owner_wxid='wxid_owner',owner_name='真实主人')
        self.ctx.llm.complete_json.return_value={'decision':'direct','reply':'你是我的主人。'}
        self.step()
        self.assertNotEqual(self.ctx.reply.call_args.args[1],'你是我的主人。')
        import json
        payload=json.loads(self.ctx.llm.complete_json.call_args.args[1])
        self.assertFalse(payload['current_speaker']['is_owner'])
        self.assertEqual(payload['current_speaker']['wxid'],'a')
    def test_natural_image_action_enqueues_without_text_reply(self):
        from unittest.mock import Mock
        self.plugin.images=Mock()
        self.ctx.llm.complete_json.return_value={'decision':'direct','action':'image_generation','image_prompt':'小猫','reply':''}
        self.step()
        self.plugin.images.submit.assert_called_once()
        self.ctx.reply.assert_not_called()
    def test_explicit_image_command_bypasses_conversation_slash_filter(self):
        from unittest.mock import Mock,patch
        self.plugin.images=Mock()
        self.msg.text='/画图 小猫'
        self.msg.data={'native_message_id':'20'}
        with patch.object(__import__('tests.test_natural_chat',fromlist=['plugin_module']).plugin_module.time,'monotonic',return_value=1):
            self.plugin.on_message(self.msg,self.ctx)
        self.plugin.images.submit.assert_called_once()

class ImageRoutingParity(unittest.TestCase):
    def test_image_intent_is_distinct_from_gallery_and_discussion(self):
        from wxbot_framework.image_generation import image_intent
        self.assertTrue(image_intent('/画图 小猫'))
        self.assertTrue(image_intent('帮我生成一张小猫图片'))
        self.assertFalse(image_intent('/来张图'))
        self.assertFalse(image_intent('这张图不错'))

class ImageDispatchParity(unittest.TestCase):
    def test_active_game_does_not_consume_natural_image_request(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from wxbot_framework.plugins import PluginManager
        from wxbot_framework.sdk import Message
        import threading
        manager=object.__new__(PluginManager);manager._lock=threading.RLock()
        game=SimpleNamespace(active=True,instance=SimpleNamespace(claims_message=lambda m:True),spec=SimpleNamespace(name='idiom_chain',message_types={1}),enqueue=Mock())
        ai=SimpleNamespace(active=True,instance=SimpleNamespace(images=object()),spec=SimpleNamespace(name='natural_chat',message_types={1}),enqueue=Mock())
        manager.records={'natural_chat':ai,'idiom_chain':game}
        manager.dispatch(Message('帮我生成一张猫图片','1@chatroom','wxid_sender',is_group=True))
        ai.enqueue.assert_called_once()
        game.enqueue.assert_not_called()

class OwnerBindingParity(PanelFixture):
    def test_owner_is_resolved_only_from_real_room_member_id(self):
        from types import SimpleNamespace
        self.controller.save_configuration({'groups':['1@chatroom']})
        self.controller.store.bind_account('wxid_robot')
        class Service:
            def group_members(self,room,*,expected_session):
                return [{'wxid':'wxid_real','nickname':'真实名字'}]
        app=SimpleNamespace(backend=SimpleNamespace(session_id='s'),service=Service(),apply_runtime_configuration=lambda changed:None)
        self.controller.app=app;self.controller.state='running'
        try:
            self.controller._save_configuration({'natural_chat':{'group_settings':{'1@chatroom':{'owner_wxid':'wxid_real','owner_name':'伪造名字'}}}},app)
            owner=self.controller.configuration()['natural_chat']['group_settings']['1@chatroom']
            self.assertEqual(owner['owner_name'],'真实名字')
            with self.assertRaises(ValueError):
                self.controller._save_configuration({'natural_chat':{'group_settings':{'1@chatroom':{'owner_wxid':'wxid_robot'}}}},app)
        finally:
            self.controller.app=None;self.controller.state='stopped'

class ImageCancellationParity(unittest.TestCase):
    def test_reconfigure_during_cloud_call_prevents_send_and_replay_after_restart(self):
        from wxbot_framework.image_generation import ImageGeneration
        import json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            root.joinpath('image_config.json').write_text(json.dumps({'enabled':True,'base_url':'https://example.com','model':'x','api_key':'test','cooldown_seconds':0}))
            delivered=[]
            class Client:
                def __init__(self,config):pass
                def generate(self,prompt,*,current):
                    worker.invalidate()
                    return b'image'
            worker=ImageGeneration(root,client_factory=Client)
            request=dict(room='1@chatroom',source='1',prompt='猫',current=lambda:True,deliver=lambda *args:delivered.append(True))
            self.assertTrue(worker.submit(**request))
            worker.step()
            self.assertEqual(delivered,[])
            restarted=ImageGeneration(root,client_factory=Client)
            self.assertFalse(restarted.submit(**request))

class NaturalHotSwitchParity(unittest.TestCase):
    setUp = natural_tests.NaturalPluginTests.setUp
    def test_master_disable_clears_rooms_and_invalidates_queued_images(self):
        from unittest.mock import Mock
        self.plugin.images=Mock()
        self.plugin._queue_image(self.ctx,self.msg,'猫')
        current=self.plugin.images.submit.call_args.kwargs['current']
        self.assertTrue(current())
        self.plugin.apply_settings(dict(self.ctx.config,enabled=False),self.ctx)
        self.assertFalse(current())
        self.assertEqual(self.plugin.states,{})
    def test_runtime_reconciles_off_and_on_plugin_lifecycle(self):
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from wxbot_framework.runtime import BotApplication
        options={'enabled':False}
        record=SimpleNamespace(active=True,callback_lock=threading.RLock(),instance=Mock(),context=SimpleNamespace())
        manager=SimpleNamespace(records={'natural_chat':record},disable=Mock(),load_one=Mock())
        app=SimpleNamespace(store=SimpleNamespace(load=lambda:{'plugins':{'natural_chat':options}}),config={},members=None,plugins=manager,backend=None)
        BotApplication.apply_runtime_configuration(app,{'natural_chat'})
        manager.disable.assert_called_once_with('natural_chat',persist=False)
        manager.records={};options['enabled']=True
        BotApplication.apply_runtime_configuration(app,{'natural_chat'})
        manager.load_one.assert_called_once_with('natural_chat')
