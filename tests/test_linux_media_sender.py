import io,tempfile,unittest
from pathlib import Path
from PIL import Image
from linux_native.media_sender import verify_staged_media,MediaDriver

class MediaSenderTests(unittest.TestCase):
 def test_file_dialog_click_is_relative_to_its_client_window(self):
  actions=[]
  class Driver(MediaDriver):
   def dialog(self):return 'dialog'
   def command(self,*args):actions.append(args);return ''
  Driver.__new__(Driver).dialog_click(200,354)
  self.assertEqual(actions,[('mousemove','--window','dialog','200','354','click','1')])
 def test_selected_attachment_uses_send_button_instead_of_return(self):
  actions=[]
  class Driver(MediaDriver):
   def __init__(self):self.width=878;self.height=640;self.prepared_name='bot'
   def assert_focus(self):pass
   def assert_no_modal(self):pass
   def title(self):return 'bot(3)'
   def click(self,x,y):actions.append(('click',round(x*self.width),round(y*self.height)))
   def key(self,*keys):raise AssertionError('Return can leave the selected attachment staged')
  Driver().commit()
  self.assertEqual(actions,[('click',829,612)])
 def test_attachment_commit_cannot_click_when_focus_changed(self):
  class Driver(MediaDriver):
   def assert_focus(self):raise ValueError('Client window lost focus')
   def click(self,*args):raise AssertionError('Do not click another window')
  with self.assertRaisesRegex(ValueError,'lost focus'):Driver.__new__(Driver).commit()
 def test_clipboard_image_must_match_the_selected_file_pixels(self):
  with tempfile.TemporaryDirectory() as folder:
   f=Path(folder)/'test.png';Image.new('RGB',(5,4),'green').save(f)
   self.assertTrue(verify_staged_media('image',f,{'image/png':f.read_bytes(),'text/plain':b''}))
   data=io.BytesIO();Image.new('RGB',(5,4),'red').save(data,format='PNG')
   with self.assertRaises(ValueError):verify_staged_media('image',f,{'image/png':data.getvalue(),'text/plain':b''})
 def test_file_clipboard_requires_one_exact_local_uri(self):
  with tempfile.TemporaryDirectory() as folder:
   f=Path(folder)/'test.txt';f.write_text('test')
   self.assertTrue(verify_staged_media('file',f,{'text/uri-list':f.as_uri().encode()}))
   with self.assertRaises(ValueError):verify_staged_media('file',f,{'text/uri-list':(f.as_uri()+'\nfile:///elsewhere').encode()})
