# 中文使用介绍

成片：intro-zh-CN.mp4，1920×1080，24fps，约4分41秒，中文配音与内嵌字幕。独立字幕为intro-zh-CN.srt，完整旁白narration.mp3；分镜脚本script.md和timing.json可复用。

管理台画面来自真实程序的合成配置演示环境，没有启动微信或模型工作进程，示例群/账号不是真实用户。群聊结果截图经过裁剪，移除群名、成员昵称、头像和联系人列表。太空鲸鱼是实际MiniMax生图结果；视频中的该实测样本含人工恢复，未冒充新自动回合验收。

旁白使用MiniMax speech-2.8-hd合成，未包含真实密钥。没有使用背景音乐或第三方视频片段。

重新制作：安装Pillow和FFmpeg，运行 `python tools/build_intro_video.py --ffmpeg <ffmpeg路径> --font <中文字体路径>`；可复用仓库中的narration.mp3与timing.json，无需再次请求语音API。若要换旁白，可用 `--audio-dir` 指向01.wav到12.wav所在目录。素材帧由脚本生成，生成中间文件位于被忽略的.runtime/video-build。
