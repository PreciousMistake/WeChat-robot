# 自然接话（本地新增插件）

仅指定群的新文本会进入判断；无需 @。模型一次返回 direct、participate 或 ignore 及正文。
默认 MiniMax-M2.7，复用 llm_config.json，无工具执行。当前最多12条、每条500字符。

群范围来自 members_config.json 的支持群列表。管理台“人格与接话”可为每群设置
enabled、persona_id、dry_run、cooldown_seconds（默认30）、hourly_limit（默认30）。
人格库和 group_settings 存在当前账号的 bot_config.json，不需要重新打包。
模型同账号共用；上下文、去重、人格及主动额度各群隔离。单个调度器串行调用模型。
dry_run=true 只生成 .runtime/natural-chat-drafts.jsonl；false 才发送。
主动额度只在发送成功后计数，直接回应不受主动额度限制。每5秒最多一次判断请求，
输入聚合3秒、最长6秒；排队超过45秒、模型回复超过45秒或本群有更新消息时丢弃旧结果。
自身消息不触发。换连接时清空所有群的短期上下文。
mention_chat 应保持关闭，避免重复回复；机器人重启后频率计数及短期上下文重置。

局限：对话指向由模型判断，可能插话或漏答；候选群消息会发送给配置的模型服务。
仅支持普通文本，不解析引用媒体或识别图片；失去连接的回复由 SDK 会话校验拒绝。
停止插件时中断等待，进行中的模型 HTTP 请求最多等配置超时，但结果不会再发送。
