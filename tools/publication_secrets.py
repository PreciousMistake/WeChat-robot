"""Collect exact local model credentials for release scanning, without output."""
import json
from pathlib import Path

def model_credentials(root):
    found=set()
    def walk(value):
        if isinstance(value,dict):
            for name,item in value.items():
                if name=='api_key' and isinstance(item,str) and item.strip():found.add(item.strip())
                else:walk(item)
        elif isinstance(value,list):
            for item in value:walk(item)
    for name in ('llm_config.json','tts_config.json','asr_config.json'):
        path=Path(root)/name
        if path.exists():walk(json.loads(path.read_text(encoding='utf-8-sig')))
    return sorted(found)
