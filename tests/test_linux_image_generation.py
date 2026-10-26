import base64,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from linux_native.image_client import ImageClient,ImageError,normalize_image_config


class ImageClientTests(unittest.TestCase):
 def options(self):return dict(enabled=True,base_url='https://images.example/v1',model='image-model',api_key='private-key',size='1024x1024')
 def png(self):
  b=io.BytesIO();Image.new('RGB',(20,20),'blue').save(b,format='PNG');return b.getvalue()
 def test_base64_response_and_exact_single_image_request(self):
  client=ImageClient(self.options());calls=[]
  def post(payload,**kwargs):calls.append(payload);return {'data':[{'b64_json':base64.b64encode(self.png()).decode()}]}
  with patch.object(client,'_post',side_effect=post):image=client.generate('a blue whale')
  self.assertTrue(image);self.assertEqual(calls,[{'model':'image-model','prompt':'a blue whale','n':1,'size':'1024x1024'}])
  self.assertNotIn('private-key',json.dumps(calls))
 def test_minimax_auto_preserves_native_endpoint_and_decodes_response(self):
  for host in ('api.minimax.cn','api.minimaxi.com','api.minimax.io'):
   for ending in ('','/v1','/v1/image_generation'):
    client=ImageClient(dict(self.options(),base_url='https://'+host+ending,model='image-01'));calls=[]
    self.assertEqual(client.endpoint,'https://'+host+'/v1/image_generation')
    def post(payload,**kwargs):calls.append(payload);return {'base_resp':{'status_code':0},'data':{'image_urls':['https://cdn.example/a.png']}}
    with patch.object(client,'_post',side_effect=post),patch.object(client,'_download',return_value=self.png()):self.assertTrue(client.generate('whale'))
    self.assertEqual(calls,[{'model':'image-01','prompt':'whale','n':1,'response_format':'url','width':1024,'height':1024}])
 def test_minimax_base64_and_business_error(self):
  client=ImageClient(dict(self.options(),protocol='minimax',base_url='https://images.example/v1/image_generation',model='image-01-live'))
  with patch.object(client,'_post',return_value={'data':{'image_base64':[base64.b64encode(self.png()).decode()]},'base_resp':{'status_code':0}}) as post:
   self.assertTrue(client.generate('whale'));self.assertEqual(post.call_args.args[0]['aspect_ratio'],'1:1');self.assertNotIn('size',post.call_args.args[0])
  with patch.object(client,'_post',return_value={'base_resp':{'status_code':1008,'status_msg':'private-key secret'}}),self.assertRaisesRegex(ImageError,'1008') as error:client.generate('whale')
  self.assertNotIn('secret',str(error.exception))
 def test_minimax_rejects_invalid_dimensions_before_api(self):
  client=ImageClient(dict(self.options(),base_url='https://api.minimax.cn/v1/image_generation',model='image-01',size='513x1024'))
  with patch.object(client,'_post') as post,self.assertRaises(ImageError):client.generate('whale')
  post.assert_not_called()
 def test_minimax_empty_response_reports_counts_and_download_failures_stay_distinct(self):
  client=ImageClient(dict(self.options(),base_url='https://api.minimax.cn/v1',model='image-01'))
  with patch.object(client,'_post',return_value={'data':{'image_urls':[]},'metadata':{'success_count':'0','failed_count':'1'},'base_resp':{'status_code':0}}),self.assertRaisesRegex(ImageError,'成功 0，失败 1'):client.generate('whale')
  with patch.object(client,'_post',return_value={'data':{'image_urls':['https://cdn.example/a.png']}}),patch.object(client,'_download',side_effect=ValueError('private url')),self.assertRaisesRegex(ImageError,'下载失败') as error:client.generate('whale')
  self.assertNotIn('private',str(error.exception))
 def test_empty_or_invalid_response_is_rejected(self):
  client=ImageClient(self.options())
  for value in ({'data':[]},{'data':[{'b64_json':'invalid!'}]},{'data':[{'b64_json':base64.b64encode(b'not-image').decode()}]}):
   with self.subTest(value=value),patch.object(client,'_post',return_value=value),self.assertRaises(ImageError):client.generate('whale')
 def test_url_response_download_does_not_receive_api_credentials(self):
  client=ImageClient(self.options());calls=[]
  def download(url,**kwargs):calls.append((url,set(kwargs)));return self.png()
  with patch.object(client,'_post',return_value={'data':[{'url':'https://cdn.example/image.png'}]}),patch.object(client,'_download',side_effect=download):self.assertTrue(client.generate('whale'))
  self.assertEqual(calls,[('https://cdn.example/image.png',{'current'})])
 def test_image_url_cannot_resolve_to_an_internal_address(self):
  import socket
  client=ImageClient(self.options())
  addresses=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
  with patch('linux_native.image_client.socket.getaddrinfo',return_value=addresses),patch('linux_native.image_client.http.client.HTTPSConnection') as connection:
   with self.assertRaises(ImageError):client._download('https://cdn.example/image.png',current=lambda:True)
   connection.assert_not_called()
 def test_configuration_blocks_request_override_and_credential_urls(self):
  for change in ({'base_url':'https://secret@example.com/v1'},{'extra_body':{'prompt':'injected'}},{'extra_body':{'n':2}},{'size':'99999x99999'}):
   with self.subTest(change=change),self.assertRaises(ValueError):normalize_image_config(dict(self.options(),**change))
 def test_disabled_unconfigured_service_is_valid(self):self.assertFalse(normalize_image_config({})['enabled'])
 def test_qwen_aliyun_auto_uses_native_protocol_without_retrying_compatible_route(self):
  options=dict(self.options(),base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',model='qwen-image-2.1-turbo')
  client=ImageClient(options);calls=[]
  def post(payload,**kwargs):calls.append(payload);return {'output':{'choices':[{'message':{'content':[{'image':'https://cdn.example/image.png'}]}}]}}
  with patch.object(client,'_post',side_effect=post),patch.object(client,'_download',return_value=self.png()):self.assertTrue(client.generate('a whale'))
  self.assertEqual(client.endpoint,'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation')
  self.assertEqual(calls,[{'model':'qwen-image-2.1-turbo','input':{'messages':[{'role':'user','content':[{'text':'a whale'}]}]},'parameters':{'n':1,'size':'1024*1024'}}])
 def test_explicit_openai_protocol_is_preserved_for_aliyun(self):
  client=ImageClient(dict(self.options(),base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',model='qwen-image-2.1-turbo',protocol='openai'))
  self.assertEqual(client.endpoint,'https://dashscope.aliyuncs.com/compatible-mode/v1/images/generations')
 def test_api_total_deadline_is_checked_between_response_chunks(self):
  client=ImageClient(dict(self.options(),timeout_seconds=5));clock=[0];reads=[]
  class Response:
   status=200
   def getheader(self,key,default=None):return default
   def read(self,size):reads.append(size);clock[0]=6;return b'{'
   def close(self):pass
  class Connection:
   sock=None
   def __init__(self,*args,**kwargs):pass
   def connect(self):pass
   def request(self,*args,**kwargs):pass
   def getresponse(self):return Response()
   def close(self):pass
  with patch('linux_native.image_client.http.client.HTTPSConnection',Connection),patch('linux_native.image_client.time.monotonic',side_effect=lambda:clock[0]):
   with self.assertRaisesRegex(ImageError,'超时'):client._post({'prompt':'whale'})
  self.assertEqual(len(reads),1)
 def test_deadline_interrupts_chunk_header_after_socket_detachment(self):
  import socket,threading,http.client
  client_socket,server_socket=socket.socketpair();client_socket.settimeout(2)
  server_socket.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nTransfer-Encoding: chunked\r\n\r\n1')
  class Connection:
   sock=None
   def __init__(self,*args,**kwargs):pass
   def connect(self):self.sock=client_socket
   def request(self,*args,**kwargs):pass
   def getresponse(self):
    response=http.client.HTTPResponse(self.sock);response.begin();self.sock=None;return response
   def close(self):
    if self.sock:self.sock.close();self.sock=None
  client=ImageClient(self.options());client.options['timeout_seconds']=.15;errors=[]
  def run():
   try:client._post({'prompt':'whale'})
   except Exception as error:errors.append(error)
  try:
   with patch('linux_native.image_client.http.client.HTTPSConnection',Connection):
    thread=threading.Thread(target=run);thread.start();thread.join(.7);finished=not thread.is_alive()
    server_socket.close();thread.join(2)
   self.assertTrue(finished,'Deadline must interrupt the real chunk-header reader')
   self.assertTrue(errors);self.assertIsInstance(errors[0],ImageError)
  finally:client_socket.close();server_socket.close()


class ImageRequestTests(unittest.TestCase):
 def setup_state(self,directory):
  from linux_native.image_requests import ImageRequests
  state=Path(directory)
  (state/'ai_config.json').write_text(json.dumps({'account':'self','enabled':True,'draft_only':False,'systems':{'123@chatroom':'persona'}}))
  (state/'image_config.json').write_text(json.dumps(dict(ImageClientTests().options(),cooldown_seconds=0)))
  return state,ImageRequests(state,'self')
 def test_duplicate_source_generates_once_and_uses_guarded_image_outbox(self):
  from linux_native.plugin_jobs import PluginJobs
  with tempfile.TemporaryDirectory() as folder:
   state,requests=self.setup_state(folder);message={'chat_id':'123@chatroom','server_id':'123'}
   requests.submit(message,'a whale','persona');requests.submit(message,'a whale','persona');calls=[]
   class Client:
    def __init__(self,options):pass
    def generate(self,prompt,**kwargs):calls.append(prompt);return ImageClientTests().png()
   self.assertEqual(requests.step(client_factory=Client),'forwarded');requests.step(client_factory=Client)
   self.assertEqual(calls,['a whale'])
   with requests.db() as db:ident=db.execute('SELECT job_id FROM requests').fetchone()[0]
   jobs=PluginJobs(state,'self');job=jobs.get(ident);self.assertEqual(job['kind'],'image');self.assertTrue(jobs.valid(job))
   (state/'image_config.json').write_text(json.dumps({'enabled':False}))
   self.assertFalse(jobs.valid(job))
 def test_interrupted_call_is_not_retried(self):
  with tempfile.TemporaryDirectory() as folder:
   state,requests=self.setup_state(folder);requests.submit({'chat_id':'123@chatroom','server_id':'1'},'whale','persona')
   with requests.db() as db:db.execute("UPDATE requests SET state='generating'")
   requests.recover()
   self.assertIsNone(requests.step(client_factory=lambda options:self.fail('No repeat paid call')))
   with requests.db() as db:self.assertEqual(db.execute('SELECT state FROM requests').fetchone()[0],'uncertain')
 def test_draft_mode_never_queues_or_calls_image_api(self):
  with tempfile.TemporaryDirectory() as folder:
   state,requests=self.setup_state(folder);p=json.loads((state/'ai_config.json').read_text());p['draft_only']=True;(state/'ai_config.json').write_text(json.dumps(p))
   requests.submit({'chat_id':'123@chatroom','server_id':'1'},'whale','persona')
   self.assertIsNone(requests.step(client_factory=lambda options:self.fail('No paid call in draft mode')))
 def test_natural_model_action_is_routed_without_a_command(self):
  from linux_native.inbox import DurableInbox
  from linux_native.replies import ReplyQueue
  from linux_native.drafts import process_one
  with tempfile.TemporaryDirectory() as folder:
   state,requests=self.setup_state(folder);box=DurableInbox(state/'inbox.sqlite','self',{'123@chatroom'})
   box.ingest('s','123@chatroom',[]);box.ingest('s','123@chatroom',[dict(local_id=1,server_id='1',sender_id='other',type=1,text='机器人，请画一只鲸鱼')]);queue=ReplyQueue(box)
   class Client:
    def complete_json(self,system,user):return {'decision':'direct','action':'image_generation','image_prompt':'a whale','reply':''}
   result=process_one(queue,Client(),{'123@chatroom':'persona'},image_requests=requests,router=lambda message:self.fail('An active game must not swallow image intent'))
   self.assertEqual(result['state'],'image_request')
   with requests.db() as db:self.assertEqual(db.execute('SELECT prompt FROM requests').fetchall(),[('a whale',)])
   box.close()

 def test_changed_configuration_during_generation_discards_result(self):
  with tempfile.TemporaryDirectory() as folder:
   state,requests=self.setup_state(folder);requests.submit({'chat_id':'123@chatroom','server_id':'1'},'whale','persona')
   class Client:
    def __init__(self,options):pass
    def generate(self,*args,**kwargs):(state/'image_config.json').write_text(json.dumps({'enabled':False}));return ImageClientTests().png()
   self.assertEqual(requests.step(client_factory=Client),'cancelled')
   with requests.db() as db:self.assertIsNone(db.execute('SELECT job_id FROM requests').fetchone()[0])

 def test_participation_cannot_trigger_image_generation(self):
  from linux_native.image_requests import image_prompt
  self.assertIsNone(image_prompt({'decision':'participate','action':'image_generation','image_prompt':'whale','reply':'hello'}))

 def test_pause_resume_cannot_revive_an_inflight_image(self):
  with tempfile.TemporaryDirectory() as folder:
   state,requests=self.setup_state(folder);requests.submit({'chat_id':'123@chatroom','server_id':'1'},'whale','persona')
   class Client:
    def __init__(self,options):pass
    def generate(self,*args,**kwargs):
     ai=json.loads((state/'ai_config.json').read_text());ai['enabled']=False;ai['authorization_generation']=1;(state/'ai_config.json').write_text(json.dumps(ai))
     ai['enabled']=True;ai['authorization_generation']=2;(state/'ai_config.json').write_text(json.dumps(ai))
     return ImageClientTests().png()
   self.assertEqual(requests.step(client_factory=Client),'cancelled')

 def test_local_game_answer_does_not_call_chat_model_when_images_enabled(self):
  from linux_native.inbox import DurableInbox
  from linux_native.replies import ReplyQueue
  from linux_native.drafts import process_one
  with tempfile.TemporaryDirectory() as folder:
   state,requests=self.setup_state(folder);box=DurableInbox(state/'inbox.sqlite','self',{'123@chatroom'})
   box.ingest('s','123@chatroom',[]);box.ingest('s','123@chatroom',[dict(local_id=1,server_id='1',sender_id='other',type=1,text='七上八下')]);queue=ReplyQueue(box)
   class Client:
    def complete_json(self,*args):raise AssertionError('Local game should keep priority')
   result=process_one(queue,Client(),{'123@chatroom':'persona'},image_requests=requests,router=lambda message:True)
   self.assertEqual(result['state'],'plugin_claimed');box.close()
