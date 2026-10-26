import threading,unittest
from types import SimpleNamespace
from wxbot_framework.plugins import PluginManager
from wxbot_framework.sdk import Message

class DispatchContractTests(unittest.TestCase):
 def test_claim_is_reported_once_and_scope_is_checked_before_claim(self):
  calls=[]
  class Plugin:
   def claims_message(self,m):calls.append('claim');return True
  record=SimpleNamespace(active=True,spec=SimpleNamespace(name='game',message_types={1}),instance=Plugin(),context=SimpleNamespace(_service=SimpleNamespace(accepts=lambda m:False)),enqueue=lambda m:calls.append('enqueue') or True)
  manager=PluginManager.__new__(PluginManager);manager._lock=threading.RLock();manager.records={'game':record}
  result=manager.dispatch(Message('hi','123@chatroom'))
  self.assertEqual(calls,[]);self.assertFalse(result['claimed'])
  record.context._service.accepts=lambda m:True
  result=manager.dispatch(Message('hi','123@chatroom'))
  self.assertEqual(calls,['claim','enqueue']);self.assertTrue(result['claimed']);self.assertEqual(result['claimants'],['game'])
