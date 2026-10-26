"""Initialize dedicated Linux state without overwriting existing files."""
import argparse
import json
import os
import re
from pathlib import Path
from linux_native.admin import atomic_json
from wxbot_framework.account import validate_account


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--lab-root', type=Path, default=Path('/opt/wechat-native-lab'))
    parser.add_argument('--account', required=True)
    parser.add_argument('--storage', required=True)
    args = parser.parse_args()
    validate_account(args.account)
    if not re.fullmatch(re.escape(args.account) + r'_[a-zA-Z0-9]+', args.storage):
        raise ValueError('Storage must belong to this account')
    root = args.lab_root.resolve()
    state = root / 'home/.local/share/wechat-access'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(state, 12000, 12000)
    values = {
        'ai_config.json': {'account': args.account, 'enabled': False, 'draft_only': True,
                           'systems': {}, 'group_settings': {}, 'activation_after': {}, 'authorization_generation': 0},
        'groups.json': {'account': args.account, 'groups': []},
        'llm_config.json': {'base_url': 'https://api.minimax.cn/v1', 'model': 'MiniMax-M3', 'api_key': ''},
        'asr_config.json': {'enabled': False, 'provider': 'dashscope', 'model': 'qwen-audio-3.1-asr-flash-message', 'api_key': '', 'groups': []},
        'tts_config.json': {'enabled': False, 'provider': 'minimax', 'base_url': 'https://api.minimax.cn/v1', 'model': 'speech-2.8-hd', 'default_voice': 'Chinese_sweet_girl_nv1', 'api_key': ''},
        'image_config.json': {'enabled': False, 'protocol': 'auto', 'base_url': '', 'model': '', 'api_key': ''},
        'plugin_config.json': {'generation': 0, 'plugins': {}},
    }
    for name, value in values.items():
        target = state / name
        if not target.exists():
            atomic_json(target, value)
            os.chown(target, 12000, 12000)
    receiver = root / 'access/receiver.json'
    if not receiver.exists():
        atomic_json(receiver, {'container': 'wechat-native-lab', 'lab_root': str(root),
                              'account': args.account, 'storage': args.storage, 'groups': [],
                              'include_contacts': True, 'include_media': True, 'run_seconds': 0})
    print('Private state initialized; replies paused; no API key configured.')


if __name__ == '__main__':
    main()
