import hashlib
import hmac
import os
import struct
import unittest

from linux_native.keyscan import decode_buffer,masked_pattern,valid_key


class LinuxKeyScanTests(unittest.TestCase):
    def test_masked_key_requires_full_format_and_authenticated_page(self):
        key=os.urandom(32);salt=os.urandom(16);pad=os.urandom(32)
        page=bytearray(os.urandom(4096));page[:16]=salt
        mac=hashlib.pbkdf2_hmac('sha512',key,bytes(x^0x3a for x in salt),2,32)
        page[4032:]=hmac.new(mac,page[16:4032]+struct.pack('<I',1),hashlib.sha512).digest()
        literal=b"x'"+key.hex().encode()+salt.hex().encode()+b"'"
        blob=bytes(x^pad[i%32] for i,x in enumerate(literal))
        self.assertEqual(decode_buffer(blob,bytes(page)),(key,pad))
        self.assertTrue(masked_pattern(pad).fullmatch(blob))
        page[100]^=1
        self.assertIsNone(decode_buffer(blob,bytes(page)))
        self.assertFalse(valid_key(key,bytes(page)))
        self.assertIsNone(decode_buffer(blob[:-1],bytes(page)))
