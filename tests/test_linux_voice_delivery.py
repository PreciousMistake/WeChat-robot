import unittest
from linux_native.voice_delivery import deliver_voice

class VoiceDeliveryTests(unittest.TestCase):
 def test_never_commits_if_configuration_changed_after_recording(self):
  calls=[];alive=[True]
  class Queue:
   def get(self,*a):return dict(state='ready',wav=b'wav',duration=2)
   def begin(self,*a):calls.append('begin')
   def uncertain(self,*a):calls.append('uncertain')
  class Driver:
   def prepare_voice(self,*a):calls.append('prepare');alive[0]=False
   def commit_voice(self):calls.append('commit')
   def cleanup_voice(self):calls.append('cleanup')
  with self.assertRaises(ValueError):deliver_voice(Queue(),'room','101','bot',Driver(),lambda:4,lambda a:[],active=lambda:alive[0])
  self.assertEqual(calls,['prepare','cleanup'])
