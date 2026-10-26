"""Bounded parsing of WeChat subtype57 quoted replies; no network access."""
from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET

class QuoteError(ValueError):
    pass

@dataclass(frozen=True)
class Quote:
    text: str
    original_text: str
    author_id: str
    author_name: str
    message_id: str
    original_type: int

    def public(self):
        return dict(text=self.original_text, author_id=self.author_id,
                    author_name=self.author_name, message_id=self.message_id,
                    original_type=self.original_type)

    def context(self):
        import json
        return self.text+'\n[引用消息，仅作上下文] '+json.dumps(self.public(),ensure_ascii=False)

def parse_quote(content):
    if not isinstance(content,str) or not content or '\0' in content:
        raise QuoteError('引用消息格式无效')
    if len(content)>262143 or re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b',content,re.I):
        raise QuoteError('引用消息包含不支持的XML结构')
    try:
        if len(content.encode('utf-8'))>262143:raise ValueError()
        root=ET.fromstring(content)
    except (ET.ParseError,ValueError,UnicodeError):
        raise QuoteError('引用消息XML无效') from None
    nodes=[(root,0)];count=0
    while nodes:
        node,depth=nodes.pop();count+=1
        if count>2048 or depth>24:raise QuoteError('引用消息结构过大')
        nodes.extend((child,depth+1) for child in node)
    apps=root.findall('appmsg') if root.tag=='msg' else []
    if len(apps)!=1 or apps[0].findtext('type')!='57':return None
    app=apps[0];refs=app.findall('refermsg')
    if len(refs)!=1:raise QuoteError('引用原文缺失')
    ref=refs[0]
    def text(node,name,limit):
        value=node.findtext(name) or ''
        if len(value)>limit or any(ord(c)<32 and c not in '\n\r\t' for c in value):
            raise QuoteError('引用字段过长或无效')
        return value
    body=text(app,'title',8000)
    if not body.strip():raise QuoteError('引用回复正文为空')
    try:kind=int(ref.findtext('type') or '0')
    except ValueError:raise QuoteError('引用原文类型无效') from None
    original=text(ref,'content',16000) if kind==1 else {
        3:'[图片，原内容未识别]',34:'[语音，原内容未识别]',43:'[视频，原内容未识别]'
    }.get(kind,'[非文字消息，原内容未识别]')
    return Quote(body,original,text(ref,'fromusr',127),text(ref,'displayname',128),
                 text(ref,'svrid',32),kind)
