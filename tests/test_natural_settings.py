"""Offline validation and account-local panel persistence for natural conversation."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from wxbot_framework.natural_settings import normalize_settings
from wxbot_framework.web_panel import PanelController

PROJECT = Path(__file__).resolve().parents[1]
ROOM_A, ROOM_B = '111@chatroom', '222@chatroom'


class NaturalSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'personas').mkdir()
        (self.root/'personas/whale.md').write_text('鲸鱼娘喜欢白米饭。',encoding='utf-8')

    def test_defaults_follow_configured_groups(self):
        result=normalize_settings({},[ROOM_A,ROOM_B],self.root)
        self.assertFalse(result['enabled'])
        self.assertEqual(set(result['group_settings']),{ROOM_A,ROOM_B})
        self.assertEqual(result['group_settings'][ROOM_A]['persona_id'],'neutral')
        self.assertEqual(result['group_settings'][ROOM_B]['cooldown_seconds'],30)
        self.assertEqual(result['personas']['whale']['text'],'鲸鱼娘喜欢白米饭。')

    def test_independent_persona_and_frequency_roundtrip(self):
        options={'enabled':True,'personas':{'quiet':{'name':'安静同伴','text':'认真倾听。'}},
            'group_settings':{ROOM_A:{'persona_id':'whale','hourly_limit':12},
                              ROOM_B:{'persona_id':'quiet','hourly_limit':40,'name':'芜湖芜湖'}}}
        original=copy.deepcopy(options)
        result=normalize_settings(options,[ROOM_A,ROOM_B],self.root)
        self.assertEqual(options,original)
        self.assertEqual(result['group_settings'][ROOM_A]['hourly_limit'],12)
        self.assertEqual(result['group_settings'][ROOM_B]['persona_id'],'quiet')
        self.assertEqual(result['group_settings'][ROOM_B]['name'],'芜湖芜湖')

    def test_reject_unknown_room_and_invalid_persona(self):
        for options in ({'group_settings':{'999@chatroom':{}}},
                        {'group_settings':{ROOM_A:{'persona_id':'missing'}}},
                        {'personas':{'../secret':{'name':'bad','text':'bad'}}},
                        {'personas':{'neutral':{'name':'bad','text':'bad'}}},
                        {'persona_file':'../secret.txt'}):
            with self.subTest(options=options),self.assertRaises(ValueError):
                normalize_settings(options,[ROOM_A],self.root)

    def test_reject_invalid_values(self):
        for options in ({'enabled':'yes'},{'cooldown_seconds':float('nan')},
                        {'hourly_limit':True},{'hourly_limit':0},{'dry_run':1},
                        {'personas':{'x':{'name':'x','text':''}}},
                        {'personas':{'x':{'name':'x','text':'x'*20001}}},
                        {'group_settings':{ROOM_A:{'enabled':'yes'}}},
                        {'group_settings':{ROOM_A:{'cooldown_seconds':0}}}):
            with self.subTest(options=options),self.assertRaises(ValueError):
                normalize_settings(options,[ROOM_A],self.root)

    def test_neutral_and_legacy_migration(self):
        result=normalize_settings({'room':ROOM_A,'persona_file':'personas/whale.md',
            'group_settings':{ROOM_B:{'persona_id':'neutral'}}},[ROOM_A,ROOM_B],self.root)
        self.assertEqual(result['group_settings'][ROOM_A]['persona_id'],'whale')
        self.assertEqual(result['group_settings'][ROOM_B]['persona_id'],'neutral')

    def test_remove_group_prunes_stale_configuration_explicitly(self):
        result=normalize_settings({'group_settings':{ROOM_A:{},ROOM_B:{}}},
            [ROOM_A],self.root,prune_unknown=True)
        self.assertEqual(list(result['group_settings']),[ROOM_A])


class NaturalPanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for stem in ('bot_config','members_config','llm_config','tts_config'):
            shutil.copy2(PROJECT/(stem+'.example.json'),self.root/(stem+'.example.json'))
        (self.root/'personas').mkdir()
        (self.root/'personas/whale.md').write_text('鲸鱼娘',encoding='utf-8')
        self.panel=PanelController(self.root,scan=lambda:[])
        self.addCleanup(self.panel.close)

    def test_save_groups_and_natural_settings_together(self):
        self.panel.save_configuration({'groups':[ROOM_A,ROOM_B], 'natural_chat':{
            'enabled':True,'group_settings':{ROOM_A:{'persona_id':'whale'},
                ROOM_B:{'persona_id':'neutral','name':'芜湖芜湖'}}}})
        public=self.panel.configuration()
        self.assertEqual(public['natural_chat']['group_settings'][ROOM_B]['name'],'芜湖芜湖')
        self.assertNotIn('api_key',public['llm'])
        stored=json.loads((self.root/'bot_config.json').read_text(encoding='utf-8'))
        self.assertTrue(stored['plugins']['natural_chat']['enabled'])
        self.panel.save_configuration({'groups':[ROOM_B]})
        self.assertEqual(list(self.panel.configuration()['natural_chat']['group_settings']),[ROOM_B])

    def test_separate_workspace_does_not_inherit_custom_personas(self):
        self.panel.save_configuration({'groups':[ROOM_A],'natural_chat':{
            'personas':{'private':{'name':'个人角色','text':'仅此账号使用'}}}})
        second=PanelController(self.root,workspace=self.root/'other-account',scan=lambda:[])
        self.addCleanup(second.close)
        self.assertNotIn('private',second.configuration()['natural_chat']['personas'])

    def test_invalid_settings_are_not_partially_saved(self):
        before=(self.root/'bot_config.json').read_bytes()
        with self.assertRaises(ValueError):
            self.panel.save_configuration({'groups':[ROOM_A],'natural_chat':{'hourly_limit':0}})
        self.assertEqual((self.root/'bot_config.json').read_bytes(),before)

    def test_combined_payload_rejects_explicit_unknown_group(self):
        config_before=(self.root/'bot_config.json').read_bytes()
        members_before=(self.root/'members_config.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'未加入支持列表'):
            self.panel.save_configuration({'groups':[ROOM_A], 'natural_chat':{
                'group_settings':{ROOM_B:{'persona_id':'neutral'}}}})
        self.assertEqual((self.root/'bot_config.json').read_bytes(),config_before)
        self.assertEqual((self.root/'members_config.json').read_bytes(),members_before)


if __name__=='__main__':
    unittest.main()
