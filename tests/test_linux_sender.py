import sqlite3
import unittest
from linux_native.sender import resolve_group,verify_title,verify_empty_input,caption_position,ClientDriver


class SenderTests(unittest.TestCase):
    def test_small_search_popup_is_not_mistaken_for_main_window(self):
        class Driver(ClientDriver):
            def command(self,*args):
                if args[0]=='search':return '1\n2\n'
                if args[0]=='getwindowpid':return '29'
                if args[0]=='getwindowgeometry':return 'X=10\nY=10\nWIDTH='+('875' if args[-1]=='1' else '300')+'\nHEIGHT=640\n'
                raise AssertionError(args)
        self.assertEqual(Driver(29).window,'1')

    def test_ocr_caption_requires_one_complete_matching_line(self):
        header='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
        line='5\t1\t1\t1\t1\t1\t12\t24\t80\t20\t95\tlinux测试2\n'
        self.assertEqual(caption_position(header+line,'linux测试2'),(52.0,34.0))
        avatar='5\t1\t1\t1\t1\t0\t0\t24\t8\t20\t95\trm\n'
        self.assertEqual(caption_position(header+avatar+line,'linux测试2',min_left=10),(52.0,34.0))
        timestamp='5\t1\t1\t1\t1\t2\t250\t24\t40\t20\t95\t09:00\n'
        self.assertEqual(caption_position(header+line+timestamp,'linux测试2'),(52.0,34.0))
        self.assertIsNone(caption_position(header+line.replace('linux测试2','linux测试22'),'linux测试2'))
        with self.assertRaises(ValueError):caption_position(header+line+line.replace('\t1\t1\t12','\t2\t1\t12'),'linux测试2')
        group_header='5\t1\t2\t1\t1\t1\t12\t60\t80\t20\t95\tGroup Chats\n'
        group_line=line.replace('\t1\t1\t1\t1\t1\t12\t24','\t1\t2\t1\t2\t1\t12\t90')
        self.assertEqual(caption_position(header+line+group_header+group_line,'linux测试2',group_only=True),(52.0,100.0))

    def test_unicode_group_and_title_are_supported_without_relaxing_identity(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,is_in_chat_room INTEGER)')
        db.execute('INSERT INTO contact VALUES("123@chatroom","linux测试2","",1)')
        self.assertEqual(resolve_group(db,'123@chatroom'),'linux测试2')
        verify_title('linux 测试 2（3）','linux测试2')
        with self.assertRaises(ValueError):verify_title('linux测试3(3)','linux测试2')
        db.close()
    def test_invisible_whitespace_does_not_count_as_an_unsent_message(self):
        for value in ('',' ','\u00a0','\n','sentinel'):
            verify_empty_input(value,'sentinel')
        with self.assertRaises(ValueError):verify_empty_input('尚未发送的文字','sentinel')

    def test_target_requires_exact_unique_joined_contact(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,is_in_chat_room INTEGER)')
        db.execute('INSERT INTO contact VALUES("123@chatroom","bot","",1)')
        self.assertEqual(resolve_group(db,'123@chatroom'),'bot')
        db.execute('INSERT INTO contact VALUES("456@chatroom","bot","",1)')
        with self.assertRaises(ValueError):resolve_group(db,'123@chatroom')
        db.close()

    def test_same_searchable_remark_is_rejected(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,is_in_chat_room INTEGER)')
        db.executemany('INSERT INTO contact VALUES(?,?,?,1)', [('123@chatroom','bot',''),('456@chatroom','other','bot')])
        with self.assertRaises(ValueError):resolve_group(db,'123@chatroom')
        db.close()

    def test_direct_contact_mimicking_group_title_is_rejected(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,is_in_chat_room INTEGER)')
        db.executemany('INSERT INTO contact VALUES(?,?,?,1)', [('123@chatroom','bot',''),('friend','other','bot (3)')])
        with self.assertRaises(ValueError):resolve_group(db,'123@chatroom')
        db.close()

    def test_ocr_title_must_have_exact_name_and_group_member_count(self):
        verify_title('bot (3)\n','bot')
        for text in ('bot','bot2 (3)','other (3)','bot (3) extra'):
            with self.assertRaises(ValueError):verify_title(text,'bot')

    def test_group_candidate_fallback_requires_group_section_and_result(self):
        from linux_native.sender import first_group_result_position
        header='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
        line=lambda number,left,top,width,text:f'5\t1\t1\t1\t{number}\t1\t{left}\t{top}\t{width}\t30\t90\t{text}\n'
        internet=line(1,40,20,240,'Internet search results')+line(2,170,90,150,'target')
        self.assertIsNone(first_group_result_position(header+internet,min_left=165))
        group=line(3,40,300,200,'Group Chats')
        self.assertIsNone(first_group_result_position(header+internet+group,min_left=165))
        self.assertEqual(first_group_result_position(header+internet+group+line(4,210,410,100,'unreadable'),min_left=165),(240,425))
        self.assertEqual(first_group_result_position(header+internet+group+line(4,210,410,100,'unreadable').replace('\t90\t','\t10\t'),min_left=165),(240,425))

    def test_candidate_navigation_still_rejects_a_different_group_title(self):
        class Driver(ClientDriver):
            def __init__(self):self.window='main';self.width=878;self.height=640
            def assert_focus(self):pass
            def assert_no_modal(self):pass
            def title(self):return 'different(3)'
            def _choose_caption(self,*args,**kwargs):return True
        with self.assertRaisesRegex(ValueError,'target group'):Driver().select('bot')

    def test_search_waits_for_a_delayed_group_result(self):
        class Driver(ClientDriver):
            def __init__(self):self.window='main';self.width=878;self.height=640;self.attempts=0;self.chosen=False
            def assert_focus(self):pass
            def assert_no_modal(self):pass
            def command(self,*args):return ''
            def title(self):return 'bot(3)' if self.chosen else 'other(3)'
            def click(self,*args):pass
            def key(self,*args):pass
            def clipboard(self,*args):pass
            def read_clipboard(self):return 'bot'
            def _choose_caption(self,*args,group_only=False):
                if not group_only:return False
                self.attempts+=1;self.chosen=self.attempts>=2;return self.chosen
        from unittest.mock import patch
        d=Driver()
        with patch('linux_native.sender.time.sleep'):d.select('bot')
        self.assertEqual(d.attempts,2)

    def test_maximized_chat_uses_fixed_sidebar_and_bottom_anchored_editor(self):
        from linux_native.sender import chat_regions
        for width,height in ((878,640),(1280,900)):
            regions=chat_regions(width,height)
            left,top,right,bottom=regions['title']
            self.assertLess(left,310);self.assertGreater(right,400)
            self.assertLess(top,55);self.assertGreater(bottom,55)
            self.assertLess(regions['search'][0],295);self.assertLess(regions['search'][1],76)
            self.assertGreater(regions['input'][1],height-130)
            self.assertLess(regions['input'][1],height-40)

    def test_copy_verification_waits_for_qt_clipboard_update(self):
        from linux_native.sender import wait_clipboard
        values=iter(['old','old','new'])
        self.assertEqual(wait_clipboard(lambda:next(values),'new',timeout=.5,interval=.001),'new')
        with self.assertRaises(ValueError):wait_clipboard(lambda:'old','new',timeout=.001,interval=.001)

    def test_current_verified_group_does_not_reclick_sidebar(self):
        class Driver(ClientDriver):
            def __init__(self):self.window='main'
            def assert_focus(self):pass
            def assert_no_modal(self):pass
            def command(self,*args):raise AssertionError('Do not reactivate the focused native window')
            def title(self):return 'bot(3)'
            def _choose_caption(self,*args,**kwargs):raise AssertionError('Do not disturb already verified chat')
        Driver().select('bot')

    def test_select_does_not_interact_when_focus_is_elsewhere(self):
        class Driver(ClientDriver):
            def __init__(self):self.window='main'
            def assert_focus(self):raise ValueError('Client window lost focus')
            def assert_no_modal(self):pass
            def command(self,*args):raise AssertionError('Do not activate another window')
            def title(self):raise AssertionError('Check focus before reading the title')
        with self.assertRaisesRegex(ValueError,'lost focus'):Driver().select('bot')

    def test_same_process_modal_blocks_even_when_main_has_focus(self):
        class Driver(ClientDriver):
            def __init__(self):self.window='1';self.pid=29
            def command(self,*args):return '1\n2\n'
        from unittest.mock import patch
        from types import SimpleNamespace
        with patch('linux_native.sender.subprocess.run',return_value=SimpleNamespace(stdout='_NET_WM_STATE(ATOM) = _NET_WM_STATE_MODAL')):
            with self.assertRaisesRegex(ValueError,'modal'):Driver().assert_no_modal()

    def test_prepare_replaces_clipboard_before_copying_input(self):
        class Driver(ClientDriver):
            def __init__(self):self.width=878;self.height=640;self.copied=False;self.clip='';self.clip_values=[]
            def select(self,name):pass
            def click(self,*args):pass
            def ensure_empty_input(self):pass
            def clipboard(self,text):self.clip=text;self.clip_values.append(text)
            def key(self,*keys):
                if keys==('ctrl+a','ctrl+c'):
                    self.copied=True
                    # Copy failed: the old clipboard remains, as with a modal.
            def read_clipboard(self):return self.clip
            def title(self):return 'bot(3)'
        from unittest.mock import patch
        driver=Driver()
        from linux_native.sender import wait_clipboard
        with patch('linux_native.sender.wait_clipboard',side_effect=lambda read,expected: wait_clipboard(read,expected,timeout=.001,interval=.001)):
            with self.assertRaisesRegex(ValueError,'does not match'):driver.prepare('bot','expected reply')
        self.assertEqual(len(driver.clip_values),2)
        self.assertNotEqual(driver.clip_values[-1],'expected reply')
