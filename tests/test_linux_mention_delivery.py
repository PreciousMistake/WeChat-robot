import unittest
from linux_native.mention_delivery import deliver_mentions


class MentionDeliveryTests(unittest.TestCase):
    def test_confirmed_send_never_runs_draft_cleanup(self):
        events=[]
        class Jobs:
            def set_prepared_text(self,*args):pass
            def begin(self,*args):events.append('begin')
            def get(self,*args):return {}
            def proof_matches(self,*args):return True
            def confirm(self,*args):events.append('confirmed')
            def fail(self,*args):events.append('failed')
        class Driver:
            prepared_text='@Alpha hello'
            def prepare_at(self,*args):pass
            def commit(self):events.append('commit')
            def cleanup_at(self):events.append('cleanup')
        result=deliver_mentions(Jobs(),{'id':'job','payload':{'text':'hello'}},'bot',[],Driver(),lambda:4,lambda after:[{'server_id':'10'}])
        self.assertTrue(result)
        self.assertEqual(events,['begin','commit','confirmed'])

    def test_ambiguous_commit_never_runs_more_ui_actions(self):
        events=[]
        class Jobs:
            def set_prepared_text(self,*args):pass
            def begin(self,*args):pass
            def fail(self,*args):events.append('failed')
        class Driver:
            prepared_text='@Alpha hello'
            def prepare_at(self,*args):pass
            def commit(self):raise TimeoutError('unknown commit result')
            def cleanup_at(self):events.append('cleanup')
        with self.assertRaises(TimeoutError):
            deliver_mentions(Jobs(),{'id':'job','payload':{'text':'hello'}},'bot',[],Driver(),lambda:4,lambda after:[])
        self.assertEqual(events,['failed'])
