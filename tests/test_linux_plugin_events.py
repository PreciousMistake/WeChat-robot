import hashlib
import tempfile
import unittest
from pathlib import Path
from linux_native.plugin_events import EventStore, parse_file_metadata, resolve_file

ROOM='bot@chatroom'
REVOKE='<sysmsg type="revokemsg"><revokemsg><content>redacted</content><revoketime>1</revoketime></revokemsg></sysmsg>'

def row(kind=1, **extra):
    return dict(local_id=30,server_id='101',native_message_id='101',sender_id='friend',type=kind,text='original',**extra)

class PluginEventsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=EventStore(self.root,'self')
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def test_revoke_same_row_preserves_author_and_original_identity(self):
        self.assertEqual(self.store.observe('s',ROOM,[row()],baseline=True),0)
        self.assertEqual(self.store.observe('s',ROOM,[row(10000,raw_content=REVOKE)]),1)
        event=self.store.pending()[0];p=event['payload']
        self.assertEqual(p['recall'],{'chat_id':ROOM,'message_id':'101'})
        self.assertEqual((p['sender_id'],p['type'],p['text']),('friend',10000,''))
        self.assertTrue(0<int(p['server_id'])<2**63)
        self.assertEqual(self.store.observe('s',ROOM,[row(10000,raw_content=REVOKE)]),0)
        self.store.finish(event['id']);self.assertEqual(self.store.pending(),[])
    def test_restart_and_revisit_never_replay(self):
        self.store.observe('s',ROOM,[row()]);self.store.close();self.store=EventStore(self.root,'self')
        self.assertEqual(self.store.observe('s',ROOM,[row(10000,raw_content=REVOKE)]),1)
        self.assertEqual(self.store.originals()[0]['text'],'original')
        self.store.finish(self.store.pending()[0]['id']);self.store.close();self.store=EventStore(self.root,'self')
        self.assertEqual(self.store.observe('s',ROOM,[row(10000,raw_content=REVOKE)]),0)
    def test_untrusted_xml_missing_original_changed_server_and_baseline_do_not_emit(self):
        self.store.observe('s',ROOM,[row(10000,raw_content=REVOKE)])
        self.assertEqual(self.store.pending(),[])
        self.store.observe('s',ROOM,[row()])
        self.assertEqual(self.store.observe('s',ROOM,[row(raw_content=REVOKE)]),0)
        changed=row(10000,raw_content=REVOKE);changed['server_id']='102';changed['native_message_id']='102'
        self.assertEqual(self.store.observe('s',ROOM,[changed]),0)
        self.store.observe('s',ROOM,[row()])
        self.assertEqual(self.store.observe('s',ROOM,[row(10000,raw_content=REVOKE)],baseline=True),0)
    def test_unsafe_or_wrong_system_xml_does_not_recall(self):
        for xml in ('<sysmsg', '<!DOCTYPE sysmsg><sysmsg type="revokemsg"/>','<msg><sysmsg type="revokemsg"/></msg>','<sysmsg type="other"/>'):
            self.store.observe('s',ROOM,[row()])
            self.assertEqual(self.store.observe('s',ROOM,[row(10000,raw_content=xml)]),0)
    def test_media_once_voice_excluded_and_baseline_suppressed(self):
        self.assertEqual(self.store.observe('s',ROOM,[row(3,raw_content='<msg/>')]),1)
        self.assertEqual(self.store.observe('s',ROOM,[row(3,raw_content='<msg/>')]),0)
        self.assertEqual(self.store.observe('other',ROOM,[row(34)]),0)
        self.assertEqual(self.store.observe('history',ROOM,[row(49,app_type=6)],baseline=True),0)
        self.assertEqual(self.store.observe('history',ROOM,[row(49,app_type=6)]),0)
        self.assertEqual(len(self.store.pending()),1)
    def test_account_isolation_and_persisted_originals_are_bounded(self):
        with self.assertRaises(ValueError):EventStore(self.root,'other')
        r=row();r['text']='x'*5000;r['raw_content']='x'*70000
        self.store.observe('s',ROOM,[r]);cached=self.store.originals()
        self.assertLessEqual(len(cached[0]['text']),4000)
        self.assertNotIn('raw_content',cached[0])
    def test_recall_requires_matching_stream_room_and_valid_native_ids(self):
        self.store.observe('s',ROOM,[row()])
        self.assertEqual(self.store.observe('other',ROOM,[row(10000,raw_content=REVOKE)]),0)
        self.assertEqual(self.store.observe('s','other@chatroom',[row(10000,raw_content=REVOKE)]),0)
        bad=row(3);bad['server_id']='invalid'
        self.assertEqual(self.store.observe('s',ROOM,[bad]),0)
    def test_raw_xml_is_bounded_and_credentials_never_enter_metadata(self):
        xml='<msg><appmsg><title>x.txt</title><type>6</type><appattach><totallen>1</totallen><md5>00000000000000000000000000000000</md5><aeskey>secret</aeskey><cdnattachurl>private</cdnattachurl></appattach></appmsg></msg>'
        self.assertNotIn('secret',str(parse_file_metadata(xml)))
        self.assertNotIn('private',str(parse_file_metadata(xml)))
        self.store.observe('s',ROOM,[row(3,raw_content='文'*30000)])
        self.assertNotIn('raw_content',self.store.pending()[0]['payload'])
    def test_file_metadata_and_unique_content_match(self):
        body=b'test';md5=hashlib.md5(body).hexdigest()
        xml=f'<msg><appmsg><title>test.txt</title><type>6</type><appattach><totallen>4</totallen><md5>{md5}</md5></appattach></appmsg></msg>'
        meta=parse_file_metadata(xml);self.assertEqual(meta['file_name'],'test.txt')
        month=self.root/'msg'/'file'/'2026-10';month.mkdir(parents=True);target=month/'renamed.txt';target.write_bytes(body)
        meta.update(type=49,app_type=6,month='2026-10')
        self.assertEqual(resolve_file(self.root,meta),target)
        (month/'duplicate.txt').write_bytes(body)
        self.assertIsNone(resolve_file(self.root,meta))
    def test_file_wrong_kind_bad_month_md5_size_and_symlink_rejected(self):
        month=self.root/'msg'/'file'/'2026-10';month.mkdir(parents=True);f=month/'x.txt';f.write_bytes(b'x')
        m=dict(type=49,app_type=6,month='2026-10',file_name='x.txt',size=1,md5=hashlib.md5(b'x').hexdigest())
        for key,value in (('type',3),('app_type',5),('month','../2026-10'),('size',2),('md5','x')):
            self.assertIsNone(resolve_file(self.root,dict(m,**{key:value})))
        try:
            link=month/'link.txt';link.symlink_to(f);f.unlink()
        except OSError:return
        self.assertIsNone(resolve_file(self.root,m))
    def test_invalid_metadata_has_no_credentials(self):
        self.assertIsNone(parse_file_metadata('<!DOCTYPE msg><msg/>'))
        self.assertIsNone(parse_file_metadata('<msg><appmsg><type>5</type></appmsg></msg>'))

if __name__=='__main__':unittest.main()
