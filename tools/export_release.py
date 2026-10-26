"""Export an allowlisted source snapshot; no personal configs or runtime data."""
import argparse,hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DIRS=('wxbot_framework','linux_native','plugins','native','webui','tests','personas','media/intro','docs/release')
FILES=('README.md','LICENSE','THIRD_PARTY_NOTICES.md','VERSION','CHANGELOG.md','main.py','web.py','start_web.bat','start_web.ps1','start_bot.ps1','requirements.txt','requirements-voice.txt','.gitignore','.gitattributes')
TOOLS=('init_config.py','run_tests.py','check_publication.py','publication_secrets.py','export_release.py','check_frozen_release.py','build_intro_video.py','video_demo.py','init_linux_state.py')
DEPLOY=('Dockerfile','compose.yaml','desktop.py','wechat-native-ai.service','wechat-native-panel.service','wechat-native-receiver.service','receiver.example.json')

def export(target):
 target=target.resolve()
 if target==ROOT or ROOT in target.parents:raise ValueError('Choose a separate release checkout')
 target.mkdir(parents=True,exist_ok=True);selected=[]
 for directory in DIRS:
  for path in (ROOT/directory).rglob('*'):
   if not path.is_file() or path.is_symlink() or any(part in ('__pycache__','.runtime','.pytest_cache','.build') for part in path.relative_to(ROOT).parts):continue
   rel=path.relative_to(ROOT).as_posix()
   if path.suffix.lower() in ('.pyc','.obj','.pdb','.lib','.exp','.exe','.sqlite','.sqlite3','.db','.log','.bak'):continue
   if path.suffix.lower()=='.dll' and rel not in ('native/build_text/text_bridge.dll','native/build/selftest_bridge.dll'):continue
   selected.append(path)
 selected += [ROOT/name for name in FILES]
 selected += list(ROOT.glob('*.example.json'))
 selected += [ROOT/'tools'/name for name in TOOLS]
 selected += [ROOT/'deploy/linux-native-lab'/name for name in DEPLOY]
 for source in selected:
  if not source.is_file():raise FileNotFoundError(source)
  dest=target/source.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True)
  raw=source.read_bytes()
  if source.suffix.lower() in ('.py','.md','.txt','.json','.js','.mjs','.html','.css','.yaml','.yml','.ps1','.bat','.service','.srt') or source.name in ('Dockerfile','LICENSE','VERSION','.gitignore','.gitattributes'):raw=raw.replace(b'\r\n',b'\n')
  dest.write_bytes(raw)
 hashes={p.relative_to(target).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.rglob('*')) if p.is_file() and '.git' not in p.relative_to(target).parts and '.runtime' not in p.relative_to(target).parts and '__pycache__' not in p.relative_to(target).parts and p.name!='SHA256SUMS'}
 (target/'SHA256SUMS').write_text(''.join(v+'  '+k+'\n' for k,v in hashes.items()),encoding='utf-8')
 return {'files':len(hashes),'target':str(target)}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--target',type=Path,required=True);print(json.dumps(export(p.parse_args().target)))
