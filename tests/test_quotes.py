import unittest
from wxbot_framework.quotes import parse_quote, QuoteError

def sample(kind='1', content='原来的话'):
    return f'<msg><appmsg><title>这是什么意思？</title><type>57</type><refermsg><type>{kind}</type><svrid>123</svrid><fromusr>wxid_original</fromusr><displayname>小明</displayname><content>{content}</content></refermsg></appmsg></msg>'

class QuoteTests(unittest.TestCase):
    def test_text_and_original_author_are_distinct(self):
        quote=parse_quote(sample())
        self.assertEqual(quote.text,'这是什么意思？')
        self.assertEqual(quote.original_text,'原来的话')
        self.assertEqual(quote.author_id,'wxid_original')
        self.assertEqual(quote.author_name,'小明')

    def test_other_app_messages_are_not_quotes(self):
        self.assertIsNone(parse_quote('<msg><appmsg><type>6</type></appmsg></msg>'))

    def test_nontext_original_is_not_treated_as_plain_text(self):
        quote=parse_quote(sample('34','voice metadata'))
        self.assertEqual(quote.original_text,'[语音，原内容未识别]')

    def test_bounded_and_entity_free(self):
        for content in ('<msg>', '<!DOCTYPE msg [<!ENTITY x "secret">]>'+sample(),sample(content='x'*20000)):
            with self.assertRaises(QuoteError):parse_quote(content)

    def test_missing_reference_fails(self):
        with self.assertRaises(QuoteError):
            parse_quote('<msg><appmsg><type>57</type><title>你好</title></appmsg></msg>')

    def test_escaped_text_decodes_once(self):
        quote=parse_quote(sample(content='&lt;hello&gt;'))
        self.assertEqual(quote.original_text,'<hello>')
