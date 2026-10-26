import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from wxbot_framework.plugins import PluginManager
from wxbot_framework.sdk import Message
from wxbot_framework.game_routing import GameRouting

class RoutingTests(unittest.TestCase):
    def message(self,text,room='123@chatroom',session='s',**kw):
        return Message(text,room,'sender',is_group=True,session_id=session,**kw)

    def test_game_owns_opening_pending_answers_and_releases_on_exit(self):
        router=GameRouting({'成语接龙'},{'退出接龙'},{'合并接龙'},lambda text:not text.startswith('/'))
        start=self.message('成语接龙')
        self.assertTrue(router.claim(start,False))
        self.assertTrue(router.claim(self.message('一心一意'),False))
        self.assertFalse(router.claim(self.message('你好',room='456@chatroom'),False))
        router.complete(start,False)
        self.assertFalse(router.claim(self.message('你好'),False))
        router.claim(start,False);router.complete(start,True)
        self.assertTrue(router.claim(self.message('一心一意'),True))
        self.assertTrue(router.claim(self.message('退出接龙'),True))
        self.assertFalse(router.claim(self.message('你好'),True))
        self.assertFalse(router.claim(self.message('你好',session='next'),False))

    def test_explicit_mention_bypasses_game(self):
        router=GameRouting({'成语接龙'},{'退出接龙'},set(),lambda text:True)
        self.assertFalse(router.claim(self.message('机器人帮我解释一下',data={'is_at_self':True}),True))

    def test_manager_routes_business_claim_before_ai_enqueue(self):
        manager=object.__new__(PluginManager)
        import threading
        manager._lock=threading.RLock()
        game=SimpleNamespace(active=True,instance=SimpleNamespace(claims_message=Mock(return_value=True)),
            spec=SimpleNamespace(name='idiom_chain',message_types={1}),enqueue=Mock())
        ai=SimpleNamespace(active=True,instance=SimpleNamespace(suppress_message=Mock()),
            spec=SimpleNamespace(name='natural_chat',message_types={1}),enqueue=Mock())
        manager.records={'natural_chat':ai,'idiom_chain':game}
        message=self.message('成语接龙')
        manager.dispatch(message)
        game.enqueue.assert_called_once_with(message)
        ai.enqueue.assert_not_called()
        ai.instance.suppress_message.assert_called_once_with(message)
        game.instance.claims_message.return_value=False
        manager.dispatch(self.message('机器人你好'))
        self.assertEqual(ai.enqueue.call_count,1)

    def test_disabled_plugin_cannot_claim(self):
        manager=object.__new__(PluginManager)
        import threading
        manager._lock=threading.RLock()
        game=SimpleNamespace(active=False,instance=SimpleNamespace(claims_message=Mock(return_value=True)),
            spec=SimpleNamespace(name='idiom_chain',message_types={1}),enqueue=Mock())
        ai=SimpleNamespace(active=True,instance=SimpleNamespace(suppress_message=Mock()),
            spec=SimpleNamespace(name='natural_chat',message_types={1}),enqueue=Mock())
        manager.records={'idiom_chain':game,'natural_chat':ai}
        manager.dispatch(self.message('成语接龙'))
        ai.enqueue.assert_called_once()
        game.instance.claims_message.assert_not_called()
