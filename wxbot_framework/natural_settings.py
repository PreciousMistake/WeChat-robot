"""Validated account-local persona and per-group natural-chat settings.

Persona bodies are configuration data, never filesystem paths or executable code.
Only the bundled whale preset is read from a fixed project-owned filename.
"""
from copy import deepcopy
import math
from pathlib import Path
import re

from .text import validate_target


PERSONA_ID = re.compile(r'^[a-z][a-z0-9_-]{0,47}$')


def _boolean(value, field):
    if type(value) is not bool:
        raise ValueError(f'{field} 应为布尔值')
    return value


def _cooldown(value):
    if (type(value) not in (int, float) or not math.isfinite(value) or
            not 1 <= value <= 3600):
        raise ValueError('主动接话冷却应为 1–3600 秒')
    return value


def _hourly(value):
    if type(value) is not int or not 1 <= value <= 300:
        raise ValueError('主动接话每小时上限应为 1–300 的整数')
    return value


def _string(value, field, limit, *, multiline=False, allow_empty=False):
    if (not isinstance(value, str) or len(value) > limit or
            any(ord(char) < 32 and (not multiline or char not in '\n\r\t') for char in value)):
        raise ValueError(f'{field} 格式无效或超过 {limit} 字符')
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(f'{field} 不能为空')
    return value


def normalize_settings(options, rooms, root, *, prune_unknown=False):
    """Return independent validated settings for exactly ``rooms``.

    ``prune_unknown`` is only for reading saved settings after a group was removed;
    incoming natural-chat settings normally reject references to unconfigured rooms.
    Legacy ``room`` is retained for compatibility but no longer determines scope.
    """
    if not isinstance(options, dict):
        raise ValueError('自然接话配置应为 JSON 对象')
    if isinstance(rooms, (str, bytes)):
        raise ValueError('群配置应为群 ID 列表')
    rooms = list(dict.fromkeys(rooms))
    if len(rooms) > 1000:
        raise ValueError('最多支持 1000 个群配置')
    for room in rooms:
        validate_target(room)
        if not room.endswith('@chatroom'):
            raise ValueError('自然接话只支持以 @chatroom 结尾的群 ID')

    result = deepcopy(options)
    result['enabled'] = _boolean(options.get('enabled', False), '自然接话总开关')
    result['dry_run'] = _boolean(options.get('dry_run', True), '草稿模式')
    result['cooldown_seconds'] = _cooldown(options.get('cooldown_seconds', 30))
    result['hourly_limit'] = _hourly(options.get('hourly_limit', 30))

    legacy_path = options.get('persona_file')
    if legacy_path not in (None, '', 'personas/whale.md'):
        raise ValueError('不能通过配置读取任意人设文件，请使用人格库正文')
    personas = {}
    fixed_path = Path(root).resolve() / 'personas' / 'whale.md'
    if fixed_path.is_file():
        text = _string(fixed_path.read_text(encoding='utf-8-sig'), '内置鲸鱼娘人设', 20000, multiline=True)
        personas['whale'] = {'name': '鲸鱼娘', 'text': text}
    elif legacy_path:
        raise ValueError('缺少内置鲸鱼娘人设文件')

    custom = options.get('personas', {})
    if not isinstance(custom, dict) or len(custom) > 100:
        raise ValueError('人格库应为 JSON 对象，最多 100 项')
    for identifier, persona in custom.items():
        if (not isinstance(identifier, str) or not PERSONA_ID.fullmatch(identifier) or
                identifier == 'neutral'):
            raise ValueError('人格 ID 应为小写英文开头的字母、数字、下划线或连字符，最多 48 字符')
        if not isinstance(persona, dict) or set(persona) - {'name', 'text'}:
            raise ValueError('人格应只包含 name 和 text')
        personas[identifier] = {
            'name': _string(persona.get('name'), '人格名称', 60),
            'text': _string(persona.get('text'), '人格正文', 20000, multiline=True)}
    result['personas'] = personas

    source = options.get('group_settings', {})
    if not isinstance(source, dict) or len(source) > 1000:
        raise ValueError('每群接话配置应为 JSON 对象，最多 1000 项')
    if not prune_unknown and set(source) - set(rooms):
        raise ValueError('接话配置包含未加入支持列表的群')
    default_persona = 'whale' if legacy_path else 'neutral'
    groups = {}
    for room in rooms:
        group = source.get(room, {})
        if not isinstance(group, dict) or set(group) - {
                'enabled', 'persona_id', 'cooldown_seconds', 'hourly_limit', 'dry_run', 'name', 'reply_mode', 'owner_wxid', 'owner_name'}:
            raise ValueError('群接话配置字段无效')
        persona_id = group.get('persona_id', default_persona)
        reply_mode = group.get('reply_mode', 'auto')
        if reply_mode not in ('auto', 'text', 'voice', 'both'):
            raise ValueError('回复方式应为auto、text、voice或both')
        if (not isinstance(persona_id, str) or
                (persona_id != 'neutral' and persona_id not in personas)):
            raise ValueError('群配置引用了不存在的人格')
        groups[room] = {
            'enabled': _boolean(group.get('enabled', True), '群接话开关'),
            'owner_wxid': _string(group.get('owner_wxid', ''), '主人微信ID', 127, allow_empty=True),
            'owner_name': _string(group.get('owner_name', ''), '主人显示名称', 64, allow_empty=True),
            'reply_mode': reply_mode,
            'persona_id': persona_id,
            'cooldown_seconds': _cooldown(group.get('cooldown_seconds', result['cooldown_seconds'])),
            'hourly_limit': _hourly(group.get('hourly_limit', result['hourly_limit'])),
            'dry_run': _boolean(group.get('dry_run', result['dry_run']), '群草稿模式'),
            'name': _string(group.get('name', ''), '群显示名称', 60, allow_empty=True)}
    result['group_settings'] = groups
    return result
