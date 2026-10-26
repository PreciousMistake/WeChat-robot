from io import BytesIO
import json
import unittest
from unittest.mock import patch
import wave
from wxbot_framework.cloud_audio import ASRClient, ASRError, MiniMaxTTS, select_tts_profile
from wxbot_framework.tts import TTSError

def wav():
    out=BytesIO()
    with wave.open(out,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0'*3200)
    return out.getvalue()

class Socket:
    def __init__(self,events):self.events=iter(events);self.sent=[];self.closed=False
    def send(self,value):self.sent.append(json.loads(value))
    def send_binary(self,value):self.sent.append(value)
    def recv(self):return json.dumps(next(self.events))
    def settimeout(self,value):pass
    def close(self):self.closed=True

class ASRTests(unittest.TestCase):
    def test_final_only_and_close(self):
        socket=Socket([{'header':{'event':'task-started'}},
            {'header':{'event':'result-generated'},'payload':{'output':{'sentence':{'text':'错误中间结果','sentence_end':False}}}},
            {'header':{'event':'result-generated'},'payload':{'output':{'sentence':{'text':'你好鲸鱼娘','sentence_end':True}}}},
            {'header':{'event':'task-finished'}}])
        client=ASRClient('private')
        self.assertNotIn('private',repr(client))
        with patch('websocket.create_connection',return_value=socket):
            self.assertEqual(client.transcribe(wav()),'你好鲸鱼娘')
        self.assertTrue(socket.closed)
        self.assertEqual(socket.sent[0]['payload']['model'],'qwen-audio-3.1-asr-flash-message')

    def test_task_failure_is_sanitized(self):
        socket=Socket([{'header':{'event':'task-failed','error_message':'private token'}}])
        with patch('websocket.create_connection',return_value=socket):
            with self.assertRaises(ASRError) as caught:ASRClient('private').transcribe(wav())
        self.assertNotIn('private',str(caught.exception));self.assertTrue(socket.closed)

    def test_bad_audio_never_connects(self):
        with patch('websocket.create_connection') as connect:
            with self.assertRaises(ASRError):ASRClient('key').transcribe(b'not wav')
            connect.assert_not_called()

    def test_incomplete_response_does_not_return_partial(self):
        socket=Socket([{'header':{'event':'task-started'}},{'header':{'event':'result-generated'},'payload':{'output':{'sentence':{'text':'中间','sentence_end':False}}}},{'header':{'event':'task-finished'}}])
        with patch('websocket.create_connection',return_value=socket):
            with self.assertRaises(ASRError):ASRClient('key').transcribe(wav())

class MiniMaxTests(unittest.TestCase):
    def test_provider_switch_keeps_keys_separate(self):
        original={'provider':'fish','api_key':'fish-private','model':'s2.1-pro-free'}
        selected=select_tts_profile(original,'minimax')
        self.assertEqual(selected['api_key'],'')
        selected.update(api_key='minimax-private',model='speech-2.8-hd')
        back=select_tts_profile(selected,'fish')
        self.assertEqual(back['api_key'],'fish-private')
        again=select_tts_profile(back,'minimax')
        self.assertEqual(again['api_key'],'minimax-private')
    def test_synthesis_and_credentials_not_in_repr(self):
        client=MiniMaxTTS.from_options({'api_key':'private','model':'speech-2.8-hd','default_voice':'Chinese_sweet_girl_nv1'})
        self.assertNotIn('private',repr(client))
        class Response(BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        response=Response(json.dumps({'base_resp':{'status_code':0},'data':{'audio':wav().hex()}}).encode())
        with patch('urllib.request.OpenerDirector.open',return_value=response):
            self.assertTrue(client.synthesize('你好','Chinese_sweet_girl_nv1').startswith(b'RIFF'))

    def test_provider_error_is_sanitized(self):
        client=MiniMaxTTS.from_options({'api_key':'private'})
        response=BytesIO(json.dumps({'base_resp':{'status_code':1004,'status_msg':'private'}}).encode())
        with patch('urllib.request.OpenerDirector.open',return_value=response):
            with self.assertRaises(TTSError) as caught:client.synthesize('你好','Chinese_sweet_girl_nv1')
        self.assertNotIn('private',str(caught.exception))

    def test_endpoint_must_be_https_and_key_has_no_control_chars(self):
        for options in ({'api_key':'k','base_url':'http://example.com'},{'api_key':'k\nsecret'}):
            with self.assertRaises(TTSError):MiniMaxTTS.from_options(options)
