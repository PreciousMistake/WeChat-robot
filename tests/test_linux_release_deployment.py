import unittest
from pathlib import Path

class LinuxReleaseTests(unittest.TestCase):
 def test_voice_reader_is_in_clean_source_and_container_has_crypto_dependency(self):
  from linux_native import voice_store as linux
  from wxbot_framework import voice_store as shared
  self.assertIs(linux.decode_snapshot,shared.decode_snapshot)
  self.assertIs(linux.read_voice,shared.read_voice)
  root=Path(__file__).resolve().parents[1]
  self.assertIn('cryptography==49.0.0',(root/'deploy/linux-native-lab/Dockerfile').read_text(encoding='utf-8'))
