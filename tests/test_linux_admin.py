import json
import tempfile
import unittest
from pathlib import Path
from linux_native.admin import RuntimeController


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.state=Path(self.tmp.name)
        self.write('ai_config.json',{'account':'wxid_self','enabled':False,'draft_only':True,'systems':{'123@chatroom':'old'},'group_settings':{'123@chatroom':{'name':'bot','persona_id':'neutral','cooldown_seconds':30,'hourly_limit':30,'reply_mode':'text'}}})
        self.write('llm_config.json',{'model':'MiniMax-M3','base_url':'https://api.minimax.cn/v1','api_key':'private-model-secret','json_mode':False})
        self.write('groups.json',{'account':'wxid_self','groups':[{'id':'123@chatroom','name':'bot','remark':''},{'id':'456@chatroom','name':'linux测试2','remark':''}]})
        self.controller=RuntimeController(self.state)

    def tearDown(self):self.tmp.cleanup()

    def write(self,name,data):(self.state/name).write_text(json.dumps(data),encoding='utf-8')

    def test_public_configuration_never_returns_saved_key(self):
        data=self.controller.configuration()
        self.assertNotIn('private-model-secret',json.dumps(data))
        self.assertTrue(data['model']['api_key_set'])

    def test_image_key_is_masked_preserved_and_cannot_follow_a_changed_endpoint(self):
        self.write('image_config.json',{'enabled':True,'model':'image','base_url':'https://images.example/v1','api_key':'private-image-secret'})
        data=self.controller.configuration()
        self.assertTrue(data['image']['api_key_set']);self.assertNotIn('private-image-secret',json.dumps(data))
        self.controller.save_configuration(data)
        self.assertEqual(json.loads((self.state/'image_config.json').read_text())['api_key'],'private-image-secret')
        data=self.controller.configuration();data['image']['base_url']='https://other.example/v1'
        with self.assertRaises(ValueError):self.controller.save_configuration(data)

    def test_changing_asr_endpoint_cannot_reuse_saved_key(self):
        self.write('asr_config.json',{'enabled':True,'api_key':'private-asr-secret','model':'qwen-audio-3.1-asr-flash-message','websocket_url':'wss://dashscope.aliyuncs.com/api-ws/v1/inference','groups':[]})
        data=self.controller.configuration();data['asr']['websocket_url']='wss://example.com/inference'
        with self.assertRaises(ValueError):self.controller.save_configuration(data)
        self.assertNotIn('private-asr-secret',json.dumps(data))

    def test_blank_key_preserves_secret_and_group_scope_is_exact(self):
        data=self.controller.configuration()
        data['model']['api_key']=''
        data['groups'].append({'id':'456@chatroom','persona_id':'neutral','enabled':True,'reply_mode':'text','cooldown_seconds':10,'hourly_limit':60})
        self.controller.save_configuration(data)
        model=json.loads((self.state/'llm_config.json').read_text(encoding='utf-8'))
        ai=json.loads((self.state/'ai_config.json').read_text(encoding='utf-8'))
        self.assertEqual(model['api_key'],'private-model-secret')
        self.assertEqual(set(ai['systems']),{'123@chatroom','456@chatroom'})
        self.assertEqual(ai['group_settings']['456@chatroom']['name'],'linux测试2')

    def test_unknown_group_is_rejected_without_mutating_config(self):
        before=(self.state/'ai_config.json').read_bytes();data=self.controller.configuration()
        data['groups'].append({'id':'999@chatroom','enabled':True,'persona_id':'neutral'})
        with self.assertRaises(ValueError):self.controller.save_configuration(data)
        self.assertEqual((self.state/'ai_config.json').read_bytes(),before)

    def test_default_endpoint_cannot_reuse_saved_key_at_new_address(self):
        self.write('asr_config.json',{'api_key':'private-asr-secret','model':'qwen-audio-3.1-asr-flash-message'})
        with self.assertRaises(ValueError):
            self.controller._provider({'api_key':'','websocket_url':'wss://example.com/api'},'asr_config.json')
        self.write('llm_config.json',{'api_key':'private-model-secret','model':'chat'})
        with self.assertRaises(ValueError):
            self.controller._provider({'api_key':'','base_url':'https://example.com'},'llm_config.json')
