import unittest
from unittest.mock import patch
from pathlib import Path
from plugins.picture_idiom.render import find_font

class FontTests(unittest.TestCase):
 def test_linux_noto_font_is_a_default_fallback(self):
  with patch.object(Path,'is_file',lambda p:p.name=='NotoSansCJK-Regular.ttc'),patch('plugins.picture_idiom.render.ImageFont.truetype'):
   self.assertEqual(find_font().name,'NotoSansCJK-Regular.ttc')
