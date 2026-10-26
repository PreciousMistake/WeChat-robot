"""Shared authenticated WCDB reader used by the Linux receiver.

This compatibility module replaces the historical deployment-time file copy.
"""
from wxbot_framework.voice_store import (
    MAX_DATABASE_BYTES, MAX_VOICE_BYTES, VoiceStoreError,
    verify_key, decode_snapshot, read_voice,
)
