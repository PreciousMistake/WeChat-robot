import unittest
from linux_native.pulse import VoiceSource


class PulseTests(unittest.TestCase):
    def test_restores_default_and_unloads_modules_on_failure(self):
        calls=[]
        def call(*args):
            calls.append(args)
            if args==('get-default-source',):return 'old-mic'
            if args[:4]==('-f','json','list','source-outputs'):return '[]'
            if args[0]=='load-module':return str(sum(x[0]=='load-module' for x in calls))
            return ''
        with self.assertRaises(RuntimeError):
            with VoiceSource(call=call):raise RuntimeError('test')
        self.assertIn(('set-default-source','old-mic'),calls)
        self.assertEqual([c for c in calls if c[0]=='unload-module'],[('unload-module','2'),('unload-module','1')])

    def test_refuses_when_client_already_recording(self):
        def call(*args):
            if args[:4]==('-f','json','list','source-outputs'):
                return '[{"properties":{"application.process.binary":"wechat"}}]'
            raise AssertionError('Must not modify audio routing')
        with self.assertRaises(ValueError):
            with VoiceSource(call=call):pass
