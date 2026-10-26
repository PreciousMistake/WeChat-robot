import unittest
from linux_native.mention_sender import resolve_members,verify_at_draft

class MentionSenderTests(unittest.TestCase):
 def test_targets_need_unique_verified_native_display_names(self):
  cache={'account':'wxid_self','groups':{'123@chatroom':[{'wxid':'wxid_a','nickname':'Alpha','alias':''},{'wxid':'wxid_b','nickname':'Beta','alias':''}]}}
  self.assertEqual(resolve_members(cache,'wxid_self','123@chatroom',['wxid_a']),[{'wxid':'wxid_a','display':'Alpha'}])
  cache['groups']['123@chatroom'][1]['alias']='Alpha'
  with self.assertRaises(ValueError):resolve_members(cache,'wxid_self','123@chatroom',['wxid_a'])
  with self.assertRaises(ValueError):resolve_members(cache,'other','123@chatroom',['wxid_a'])
 def test_prepared_text_must_keep_selected_mentions_and_exact_body(self):
  self.assertTrue(verify_at_draft('@Alpha\u2005hello','hello',[{'wxid':'wxid_a','display':'Alpha'}]))
  with self.assertRaises(ValueError):verify_at_draft('@Beta hello','hello',[{'wxid':'wxid_a','display':'Alpha'}])
  with self.assertRaises(ValueError):verify_at_draft('@Alpha other','hello',[{'wxid':'wxid_a','display':'Alpha'}])
