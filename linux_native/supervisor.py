"""Privileged discovery restricted to one labelled container and bound account."""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from .keyscan import scan


def bundle(config):
    name=config['container']
    info=json.loads(subprocess.check_output(['docker','inspect',name],text=True))[0]
    if info['Config'].get('Labels',{}).get('org.weixin-helper.lab')!='native-validation':
        raise ValueError('Unexpected container ownership')
    if not info['State']['Running']:raise RuntimeError('WeChat container is not running')
    top=subprocess.check_output(['docker','top',name,'-eo','pid,comm'],text=True)
    pids=[int(line.split()[0]) for line in top.splitlines()[1:] if line.split()[-1]=='wechat']
    if len(pids)!=1:raise RuntimeError('Expected one main WeChat process')
    pid=pids[0]
    process_start=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    if info['Id'] not in Path(f'/proc/{pid}/cgroup').read_text():raise ValueError('Process is outside target container')
    account=config['account'];storage=config['storage']
    if not re.fullmatch(re.escape(account)+r'_[a-zA-Z0-9]+',storage):raise ValueError('Account storage mismatch')
    binds=[item for item in info['Mounts'] if item['Destination']=='/home/wechat' and item['Type']=='bind']
    if len(binds)!=1:raise ValueError('Dedicated home mount missing')
    home=Path(binds[0]['Source']).resolve()
    expected=Path(config['lab_root']).resolve()/'home'
    if home!=expected:raise ValueError('Home mount mismatch')
    root=(home/'xwechat_files'/storage/'db_storage').resolve()
    if not root.is_relative_to(home/'xwechat_files'):raise ValueError('Storage escapes account volume')
    pages={}
    for path in root.rglob('*.db'):
        if path.is_symlink() or not path.resolve().is_relative_to(root):continue
        with path.open('rb') as file:page=file.read(4096)
        if len(page)==4096:pages[str(path.relative_to(root))]=page
    required={name for name in pages if re.fullmatch(r'message/message_[0-9]+\.db',name)}
    if config.get('include_contacts'):
        if 'contact/contact.db' not in pages:raise RuntimeError('Contact database missing')
        required.add('contact/contact.db')
    if config.get('include_media'):
        required.update(name for name in pages if re.fullmatch(r'message/media_[0-9]+\.db',name))
    if not required:raise RuntimeError('No message database')
    keys=scan(pid,pages,required)
    if Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]!=process_start:
        raise RuntimeError('WeChat process changed during discovery')
    return {'account':account,'storage':storage,'groups':config['groups'],
            '_host_pid':pid,'_process_start':process_start,
            'run_seconds':config.get('run_seconds',0),'keys':{name:key.hex() for name,key in keys.items()}}


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True)
    config=json.loads(parser.parse_args().config.read_text())
    stopping=[False]
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stopping.__setitem__(0,True))
    payload=bundle(config)
    if stopping[0]:return
    pid=payload.pop('_host_pid');process_start=payload.pop('_process_start')
    print(json.dumps({'supervisor':'keys_ready','message_shards':len(payload['keys'])}),flush=True)
    worker=subprocess.Popen(['docker','exec','-i','--user','12000','--workdir','/home/wechat/native-access',
        config['container'],'python3','-u','-m','linux_native.worker'],stdin=subprocess.PIPE,text=True)
    try:
        worker.stdin.write(json.dumps(payload)+'\n');worker.stdin.flush()
        del payload
        while worker.poll() is None and not stopping[0]:
            try:current=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
            except FileNotFoundError:raise RuntimeError('WeChat process exited')
            if current!=process_start:raise RuntimeError('WeChat process changed')
            time.sleep(.5)
    finally:
        if worker.poll() is None:
            try:worker.stdin.write('stop\n');worker.stdin.flush()
            except BrokenPipeError:pass
        worker.stdin.close()
        try:worker.wait(timeout=10)
        except subprocess.TimeoutExpired:worker.terminate();worker.wait(timeout=5)
    if worker.returncode and not stopping[0]:raise RuntimeError('Receiver worker failed')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'supervisor':'failed','error_type':type(error).__name__}),flush=True)
        raise SystemExit(1)
