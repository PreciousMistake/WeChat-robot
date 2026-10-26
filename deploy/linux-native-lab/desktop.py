"""One private native Linux desktop; no automatic chat sends or API keys."""
import os
import json
from pathlib import Path
import secrets
import signal
import subprocess
import time

children=[]
def start(argv):
    process=subprocess.Popen(argv)
    children.append(process)
    return process

def stop(*_):
    for child in reversed(children):
        if child.poll() is None:child.terminate()
    raise SystemExit(0)

signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
os.umask(0o077)
runtime=Path('/tmp/runtime-wechat')
runtime.mkdir(exist_ok=True,mode=0o700)
os.environ['XDG_RUNTIME_DIR']=str(runtime)
subprocess.run(['xauth','add',':99','.',secrets.token_hex(16)],check=True,stdout=subprocess.DEVNULL)
xvfb=start(['Xvfb',':99','-screen','0','1280x900x24','-nolisten','tcp','-auth',os.environ['XAUTHORITY']])
for _ in range(50):
    if xvfb.poll() is not None:raise SystemExit('Virtual display failed')
    ready=subprocess.run(['xdpyinfo'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if ready.returncode==0:break
    time.sleep(.1)
else:raise SystemExit('Virtual display did not become ready')
start(['openbox'])
subprocess.run(['pulseaudio','--start','--exit-idle-time=-1'],check=True)
os.environ['PULSE_SERVER']='unix:/tmp/runtime-wechat/pulse/native'
def pulse(*args):
    return subprocess.run(['pactl',*args],capture_output=True,text=True,check=True,timeout=5).stdout.strip()
sources=json.loads(pulse('-f','json','list','sources'))
if not any(row['name']=='whale_base_mic' for row in sources):
    sinks=json.loads(pulse('-f','json','list','sinks'))
    if not any(row['name']=='whale_base_sink' for row in sinks):
        pulse('load-module','module-null-sink','sink_name=whale_base_sink','rate=48000','channels=1')
    pulse('load-module','module-remap-source','master=whale_base_sink.monitor','source_name=whale_base_mic','source_properties=device.class=sound')
pulse('set-default-source','whale_base_mic')
os.environ['PULSE_SOURCE']='whale_base_mic' 
start(['x11vnc','-display',':99','-auth',os.environ['XAUTHORITY'],'-localhost','-forever','-shared','-rfbport','5900','-nopw'])
start(['websockify','--web=/usr/share/novnc/','0.0.0.0:6080','127.0.0.1:5900'])
client=start(['/usr/bin/wechat'] if Path('/usr/bin/wechat').exists() else ['/opt/wechat/wechat'])
print('Native Linux WeChat desktop started',flush=True)
while True:
    if xvfb.poll() is not None:raise SystemExit('Virtual display exited')
    if client.poll() is not None:
        print('WeChat exited; desktop retained for diagnosis; exit code='+str(client.returncode),flush=True)
        client=subprocess.Popen(['sleep','infinity']);children.append(client)
    time.sleep(1)
