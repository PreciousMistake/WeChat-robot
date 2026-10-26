"""Scan frozen exports without displaying matching secret values."""
import argparse,json,re,hashlib
from pathlib import Path

def check(root,markers=()):
 issues=[];count=0
 for p in root.rglob('*'):
  rel=p.relative_to(root);label=rel.as_posix()
  if '.git' in rel.parts or '.runtime' in rel.parts or '__pycache__' in rel.parts:continue
  if not p.is_file():continue
  count+=1
  if p.name.endswith('_config.json') or p.name.lower() in ('apikeys.txt','minimax-apikey.txt') or p.suffix.lower() in ('.db','.sqlite','.sqlite3','.log','.pyc'):issues.append({'file':label,'kind':'private_runtime_file'})
  data=p.read_bytes()
  if any(m and len(m)>8 and m.encode() in data for m in markers):issues.append({'file':label,'kind':'known_private_value'})
  if p.suffix.lower() in ('.py','.md','.json','.js','.html','.txt','.yaml','.yml'):
   text=data.decode('utf-8',errors='replace')
   if re.search(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',text):issues.append({'file':label,'kind':'private_key'})
   if not label.startswith('tests/') and re.search(r'(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{25,})',text):issues.append({'file':label,'kind':'token_pattern'})
 manifest=root/'SHA256SUMS'
 if manifest.exists():
  for line in manifest.read_text(encoding='utf-8').splitlines():
   digest,name=line.split('  ',1);p=root/name
   if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:issues.append({'file':name,'kind':'checksum_mismatch'})
 return {'passed':not issues,'files_scanned':count,'issues':issues}
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument('--private-markers',type=Path);a=ap.parse_args();markers=json.loads(a.private_markers.read_text(encoding='utf-8')) if a.private_markers else [];r=check(a.root,markers);print(json.dumps(r,ensure_ascii=False));raise SystemExit(not r['passed'])
