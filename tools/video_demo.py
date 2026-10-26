"""Run a private, synthetic management-panel demo; never starts WeChat or AI workers."""
import json,tempfile,argparse
from pathlib import Path
from linux_native.admin import RuntimeController
from wxbot_framework.panel_http import PanelServer

def write(root,name,value):
 (root/name).write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=18768);args=parser.parse_args()
 with tempfile.TemporaryDirectory(prefix='wechat-video-demo-') as folder:
  s=Path(folder);rooms=['10000000001@chatroom','10000000002@chatroom']
  write(s,'ai_config.json',{'account':'wxid_demo_bot','enabled':False,'draft_only':True,'systems':{},'group_settings':{}})
  write(s,'groups.json',{'account':'wxid_demo_bot','groups':[{'id':r,'name':n} for r,n in zip(rooms,['演示群 A','演示群 B'])]})
  write(s,'group_members.json',{'account':'wxid_demo_bot','groups':{r:[{'wxid':'wxid_demo_owner','nickname':'演示主人'},{'wxid':'wxid_demo_member','nickname':'演示群友'}] for r in rooms}})
  write(s,'llm_config.json',{'base_url':'https://api.minimax.cn/v1','model':'MiniMax-M3','api_key':'','max_tokens':1024,'timeout_seconds':30})
  write(s,'image_config.json',{'enabled':False,'protocol':'auto','base_url':'https://api.minimax.cn/v1/image_generation','model':'image-01','api_key':''})
  write(s,'tts_config.json',{'enabled':False,'provider':'minimax','base_url':'https://api.minimax.cn/v1','model':'speech-2.8-hd','default_voice':'Chinese_sweet_girl_nv1','api_key':''})
  write(s,'asr_config.json',{'enabled':False,'provider':'dashscope','model':'qwen-audio-3.1-asr-flash-message','api_key':''})
  controller=RuntimeController(s);data=controller.configuration()
  data['groups']=[dict(id=r,name=n,enabled=True,asr_enabled=False,persona_id='whale' if i==0 else 'neutral',reply_mode='auto',owner_wxid='wxid_demo_owner' if i==0 else '',cooldown_seconds=30,hourly_limit=30,dry_run=True) for i,(r,n) in enumerate(zip(rooms,['演示群 A','演示群 B']))]
  controller.save_configuration(data)
  write(s,'plugin_config.json',{'generation':0,'plugins':{name:{'enabled':True} for name in ['echo','image_gallery','idiom_chain','picture_idiom','member_identity','chat_summary','id_responder']}})
  write(s,'capabilities.json',{'capabilities':['text.receive','text.send','group.text.receive','group.text.send','image.send','group.text.mention','group.events','file.receive']})
  server=PanelServer(controller,args.port)
  print('Synthetic demo only: http://127.0.0.1:%s/'%args.port,flush=True)
  try:server.serve_forever()
  finally:server.server_close()
if __name__=='__main__':main()
