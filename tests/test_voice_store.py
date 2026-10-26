import hashlib,hmac,sqlite3,struct,unittest
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from wxbot_framework.voice_store import decode_snapshot, query_voice, VoiceStoreError, _checksum

KEY=bytes(range(32)); SALT=bytes(range(16)); IV=bytes(reversed(range(16)))
def encrypted(number,body):
    offset=16 if number==1 else 0
    cipher=Cipher(algorithms.AES(KEY),modes.CBC(IV)).encryptor()
    data=(SALT if number==1 else b'')+cipher.update(body[offset:4016])+cipher.finalize()+IV
    mac_key=hashlib.pbkdf2_hmac('sha512',KEY,bytes(x^0x3a for x in SALT),2,32)
    return data+hmac.new(mac_key,data[offset:]+struct.pack('<I',number),hashlib.sha512).digest()

class VoiceStoreTests(unittest.TestCase):
    def test_wal_only_committed_authenticated_frames_are_applied(self):
        original=b'SQLite format 3\0'+b'a'*4000+bytes(80)
        replacement=b'SQLite format 3\0'+b'b'*4000+bytes(80)
        raw=encrypted(1,original);data=encrypted(1,replacement)
        header=struct.pack('>IIIIII',0x377f0682,3007000,4096,0,12,34)
        state=_checksum(header,'<');header+=struct.pack('>II',*state)
        def wal(commit):
            frame=struct.pack('>II',1,commit)
            checksum=_checksum(frame+data,'<',state)
            return header+frame+header[16:24]+struct.pack('>II',*checksum)+data
        self.assertEqual(decode_snapshot(raw,wal(0),KEY),original)
        self.assertEqual(decode_snapshot(raw,wal(1),KEY),replacement)
        bad=bytearray(wal(1));bad[-1]^=1
        with self.assertRaises(VoiceStoreError):decode_snapshot(raw,bytes(bad),KEY)

    def test_authenticated_pages_and_wrong_key_rejected(self):
        body=b'SQLite format 3\0'+b'a'*4000+bytes(80)
        raw=encrypted(1,body)
        self.assertEqual(decode_snapshot(raw,b'',KEY),body)
        for bad in (raw[:-1]+bytes([raw[-1]^1]),raw[:4000]):
            with self.assertRaises(VoiceStoreError):decode_snapshot(bad,b'',KEY)
        with self.assertRaises(VoiceStoreError):decode_snapshot(raw,b'',bytes(32))

    def test_query_requires_room_and_message_and_unique_audio(self):
        conn=sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.executescript('CREATE TABLE Name2Id(user_name TEXT); CREATE TABLE VoiceInfo(chat_name_id INTEGER, svr_id INTEGER, voice_data BLOB);')
        conn.executemany('INSERT INTO Name2Id VALUES (?)',[('bot@chatroom',),('other@chatroom',)])
        audio=b'\x02#!SILK_V3'+b'a'*50
        conn.executemany('INSERT INTO VoiceInfo VALUES (?,?,?)',[(1,123,audio),(2,124,audio)])
        self.assertEqual(query_voice(conn,'bot@chatroom','123'),audio)
        self.assertIsNone(query_voice(conn,'bot@chatroom','124'))
        self.assertIsNone(query_voice(conn,'missing@chatroom','123'))
        conn.execute('INSERT INTO VoiceInfo VALUES (1,123,?)',(audio,))
        with self.assertRaises(VoiceStoreError):query_voice(conn,'bot@chatroom','123')

    def test_invalid_id_and_audio_fail_closed(self):
        conn=sqlite3.connect(':memory:');self.addCleanup(conn.close)
        for ident in ('0','-1','abc','9'*30):
            with self.assertRaises(VoiceStoreError):query_voice(conn,'bot@chatroom',ident)
