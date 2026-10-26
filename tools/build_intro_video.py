"""Build the Chinese intro from actual redacted UI captures and narration audio.
Requires Pillow and FFmpeg. No cloud requests are made by this script.
"""
import argparse,json,subprocess,textwrap,wave
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,ImageOps
ROOT=Path(__file__).resolve().parents[1]
TIPS={
 'title':['微信小号群聊机器人','Linux 功能基准','Windows 使用功能对齐','封存版本 v1.0.0'],
 'overview':['准备微信小号','加入自己的测试群','保持客户端登录','按平台选择启动方式'],
 'models':['共享聊天服务','分别填写模型与地址','密钥保存在本机','不要公开运行配置'],
 'groups':['刷新已加入的群','添加到机器人配置','仅参与已启用群','各群独立维护上下文'],
 'personas':['预设或自定义人格','每群单独选择','正文保存后生效','不提供跨群长期记忆'],
 'owner':['从真实成员中选择','每群可以不同','昵称不能冒认主人','不代表后台权限'],
 'frequency':['无需每次艾特','模型判断是否回应','冷却限制主动接话','可暂停后继续配置'],
 'audio':['ASR：语音转文字','TTS：模型与音色','每群选择回复方式','先用短语音验收'],
 'image':['自然说出想画什么','或使用 /画图 描述','三种同步接口协议','超时不自动重复生成'],
 'plugins':['按需启用插件','图库与AI生图不同','游戏指令由插件接管','防止AI重复回答'],
 'health':['检查群与总开关','查看心跳与发送状态','先核对再重试','关闭网页不等于停止'],
 'end':['阅读平台启动说明','在测试群逐项验收','保留封存版本标签','后续变更建立新版本']}

def run(args,**kwargs):subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,**kwargs)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ffmpeg',default='ffmpeg');ap.add_argument('--audio-dir',type=Path);ap.add_argument('--font',default='C:/Windows/Fonts/msyh.ttc');a=ap.parse_args()
 folder=ROOT/'media/intro';assets=folder/'assets';temp=ROOT/'.runtime/video-build';temp.mkdir(parents=True,exist_ok=True)
 rows=json.loads((folder/'timing.json').read_text(encoding='utf-8'));font=lambda n:ImageFont.truetype(a.font,n)
 def frame(row,view=None):
  view=view or row['view'];im=Image.new('RGB',(1920,1080),'#102c29');d=ImageDraw.Draw(im)
  d.text((64,38),'WeChat Robot',font=font(31),fill='#d9eee5');d.text((1510,42),'v1.0.0  /  2026.10.26',font=font(23),fill='#a5c7ba')
  d.rounded_rectangle((60,135,430,204),radius=12,fill='#d9e8bd');d.text((82,149),'%02d / 12'%row['index'],font=font(29),fill='#183d32')
  y=244
  for line in textwrap.wrap(row['title'],10):d.text((60,y),line,font=font(40),fill='white');y+=58
  y+=35
  for line in TIPS[row['view']]:d.text((70,y),'· '+line,font=font(27),fill='#d2e0d7');y+=59
  if view in ('title','end'):
   whale=Image.open(assets/'space-whale.jpg').convert('RGB');whale=ImageOps.fit(whale,(1080,715));im.paste(whale,(690,148));d.text((690,884),'实际云端生图结果 · 群聊与配置教程',font=font(25),fill='#d2e0d7')
  elif view=='chat-result':
   chat=Image.open(assets/'chat-result.png').convert('RGB');chat=ImageOps.contain(chat,(920,700));im.paste(chat,(820,150));d.text((550,884),'真实测试群截图 · 本样本含人工恢复，不作为新增自动回合验收',font=font(23),fill='#d2e0d7')
  else:
   name=view if (assets/(view+'.png')).exists() else 'overview';screen=Image.open(assets/(name+'.png')).convert('RGB');screen=ImageOps.contain(screen,(1400,788));im.paste(screen,(490,140));d.text((510,923),'管理台脱敏配置演示 · 演示账号与群为合成数据',font=font(22),fill='#a5c7ba')
  d.rectangle((0,974,1920,1080),fill='#0a1c1b')
  p=temp/('%02d-%s.png'%(row['index'],view));im.save(p);return p
 audio=a.audio_dir
 durations=[r['duration']+.35 for r in rows]
 # Keep the final chapter on screen for a short outro pause.
 durations[-1]+=1
 list_audio=temp/'audio-list.txt'
 if audio:
  list_audio.write_text(''.join("file '"+str((audio/('%02d.wav'%r['index'])).resolve()).replace('\\','/')+"'\n" for r in rows),encoding='utf-8')
  run([a.ffmpeg,'-y','-f','concat','-safe','0','-i',str(list_audio),'-c:a','libmp3lame','-b:a','128k',str(folder/'narration.mp3')])
 # Timings follow the continuous narration exactly, with a final silent tail.
 durations=[r['duration'] for r in rows];durations[-1]+=2
 subs=[];cursor=0
 def ts(t):
  ms=round(t*1000);return '%02d:%02d:%02d,%03d'%(ms//3600000,ms//60000%60,ms//1000%60,ms%1000)
 for row in rows:
  chunks=textwrap.wrap(row['text'],26)
  span=row['duration']/len(chunks)
  for j,line in enumerate(chunks):subs.append('%d\n%s --> %s\n%s\n'%(len(subs)+1,ts(cursor+j*span),ts(cursor+(j+1)*span),line))
  cursor+=row['duration']
 (folder/'intro-zh-CN.srt').write_text('\n'.join(subs),encoding='utf-8')
 parts=[]
 for row,duration in zip(rows,durations):
  views=[(row['view'],duration)] if row['view']!='image' else [('image',duration*.5),('chat-result',duration*.5)]
  for view,length in views:
   png=frame(row,view);target=temp/(png.stem+'.mp4')
   run([a.ffmpeg,'-y','-loop','1','-framerate','24','-i',str(png),'-t',str(length),'-vf','scale=1940:1092,crop=1920:1080:10:6,setsar=1','-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p',str(target)])
   parts.append(target);print(json.dumps({'rendered':png.stem}),flush=True)
 playlist=temp/'video-list.txt';playlist.write_text(''.join("file '"+p.as_posix()+"'\n" for p in parts),encoding='utf-8')
 run([a.ffmpeg,'-y','-f','concat','-safe','0','-i',str(playlist),'-i',str(folder/'narration.mp3'),'-vf',"subtitles=intro-zh-CN.srt:force_style='FontName=Microsoft YaHei,FontSize=18,MarginV=10,Outline=1,Shadow=0'",'-c:v','libx264','-preset','veryfast','-crf','22','-c:a','aac','-b:a','160k','-movflags','+faststart',str(folder/'intro-zh-CN.mp4')],cwd=folder)
 Image.open(temp/'01-title.png').save(folder/'cover.jpg',quality=90)
 print(json.dumps({'video':str(folder/'intro-zh-CN.mp4'),'seconds':sum(durations)}))
if __name__=='__main__':main()
