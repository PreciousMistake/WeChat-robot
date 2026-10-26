"""沿用原项目三参数消息回调：test 返回当前会话 ID。"""


class Plugin:
    def claims_message(self,message):
        return message.type==1 and message.text.strip().lower()=='test'

    def on_message(self, msg_type, data, service):
        if msg_type != 11046:
            return
        content = data.get("msg") or data.get("content") or ""
        if content.strip().lower() == "test":
            target = data.get("room_wxid") or data.get("from_wxid")
            if target:
                service.send_text(target, target)
