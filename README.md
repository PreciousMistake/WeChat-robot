# WeChat Robot · 双平台封存版

以 Linux 已验证使用功能为基准，补齐 Windows 功能的微信小号群聊机器人。当前封存版本 **v1.0.0 · 2026-10-26**。

本项目由 [Prometheus-first/helper](https://github.com/Prometheus-first/helper) 修改而来，保留上游非商业使用许可与第三方声明。允许个人非商业研究、自用和免费分享，详细条款见 [LICENSE](LICENSE)。不是微信官方机器人服务。

## 使用介绍

[中文介绍视频](media/intro/intro-zh-CN.mp4) · [文字使用手册](docs/release/user-guide.md) · [两端功能矩阵与验证范围](docs/release/feature-matrix.md) · [封存与校验](docs/release/freeze.md)

- 小号作为普通微信群成员，支持多个已启用群。
- 无需每次艾特，由聊天模型判断回应或适度主动接话。
- 每群人格、主人、回复方式、接话频率与上下文隔离。
- 共用云端聊天模型，千问语音识别，MiniMax/Fish Audio语音合成。
- 自然语言生图与 `/画图`，支持 OpenAI兼容、Qwen原生、MiniMax原生。
- 图库、成语接龙、看图猜成语、昵称绑定、聊天总结和会话编号查询。

**具备相同的主要使用功能，不代表底层能力及真人验收完全一致。** Linux必须运行原生微信和图形会话，接收从授权账号本地数据库读取，发送通过客户端桌面操作并核对微信回执。Windows使用指定版本的原生桥接。好友自动通过、Linux进退群真人验收及无人值守恢复有明确限制，见功能矩阵。Windows本次新增生图和主人功能完成离线验证，未宣称已完成真人端到端测试。

## Windows

需要 Windows 10/11 x64、64位 Python（开发验证3.12/3.13）、Microsoft Visual C++ x64运行库及匹配构建的微信。当前桥接适配4.1.15.13 / 4.1.15.56指定指纹，未知构建会拒绝加载。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
.venv\Scripts\python.exe tools/init_config.py
.venv\Scripts\python.exe web.py
```

打开 `http://127.0.0.1:8765`，选择账号，配置群、模型与功能。也可双击 `start_web.bat`。不要同时用多个程序实例连接同一个微信进程。推荐安装到短路径，避免Windows旧式路径长度限制。

## Linux

需要 x86_64 Linux、Docker/Compose、官方Linux微信及可用图形会话。已验证客户端4.1.13.26。先阅读 [Linux部署步骤](docs/release/linux-setup.md)。源码包含桌面容器、接收监督程序、模型工作进程和本机管理台；仍需配置自己的账号、存储目录与服务，不能仅运行一个Python脚本就绕过客户端登录。

## 配置与发布

配置在各平台管理台填写；仓库只提供空白模板，**不提供真实密钥、微信登录状态、群成员数据或聊天记录**。运行配置和 `.runtime/` 应只保存在自己的机器。

本版冻结的是Git标签与源码快照，不承诺新微信版本继续兼容。若修改代码，请保留 v1.0.0 标签并建立新版本。

## 开发验证

```powershell
python tools/run_tests.py
node --check webui/app.js
node --test tests/webui_parity.test.mjs
```

安装语音依赖后运行完整测试。`imageio-ffmpeg`需可用的FFmpeg解码器，Windows测试路径不宜过长。测试不连接真实微信，不调用真实云模型；真人验收边界见版本说明。

## 引用项目、来源与致谢

以下区分“实际使用/改编”“运行依赖”和“只做过调研”。第三方代码与资料继续适用其原许可证，本项目不会把它们统一改成根目录的非商业许可。

### 实际使用或参考实现

| 项目 | 本项目中的用途与位置 | 许可与保留说明 |
|---|---|---|
| [Prometheus-first/helper](https://github.com/Prometheus-first/helper) | **主要上游框架**：Windows原生桥接、Python插件框架、原有插件和网页管理台；本版在其基础上增加Linux接入及双平台功能 | 上游[非商业使用许可](https://github.com/Prometheus-first/helper/blob/main/LICENSE)，保留在根目录LICENSE；这是有非商业限制的公开源码项目，不应表述为MIT/Apache许可项目 |
| [linsport/dsh-whale-persona](https://github.com/linsport/dsh-whale-persona) | 内置鲸鱼娘人格正文，见personas/whale.md；导入标记v8.1.1；多人群聊/JSON规则由本项目另行追加 | 原始许可保留在[personas/whale-LICENSE.txt](personas/whale-LICENSE.txt)；MIT仅覆盖上游自己创作的部分，角色视觉形象与社区来源文本按上游声明处理 |
| [TsudaKageyu/minhook](https://github.com/TsudaKageyu/minhook) | Windows桥接所用Hook库，仓库保留native/vendor/minhook-1.3.4源码及构建使用的组件 | BSD-2-Clause类许可，原文及内含HDE声明见[native/vendor/minhook-1.3.4/LICENSE.txt](native/vendor/minhook-1.3.4/LICENSE.txt) |
| [fanyuantaier/wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica) | 参考WCDB/SQLCipher布局与语音数据库读取结构；相关引用保留在native/voice_reader.py、wxbot_framework/voice_store.py头部 | 上游Apache-2.0；属于布局/实现参考，不是运行时安装wechatauto包，也不是将整个上游仓库复制进来 |
| [SQLCipher设计文档](https://www.zetetic.net/sqlcipher/design/) | 本地加密数据库页、校验和WAL读取的格式参考 | 技术文档引用；本项目未嵌入SQLCipher引擎或冒称自己实现了微信服务端协议 |

### Python与媒体依赖

| 项目 | 用途 | 许可说明 |
|---|---|---|
| [pefile](https://github.com/erocarrera/pefile) | Windows PE/DLL信息解析与构建识别 | MIT |
| [cryptography](https://github.com/pyca/cryptography) | 已授权本地数据库的加密与校验操作 | Apache-2.0或BSD双许可，按所用发行包声明 |
| [Pillow](https://github.com/python-pillow/Pillow) | 图片校验、转换、本地题图及视频画面制作 | MIT-CMU，保留PIL/Pillow版权与发行包许可 |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | 查找/提供FFmpeg工具，支持表情媒体处理 | Python包装BSD-2-Clause；FFmpeg自身许可另行适用 |
| [websocket-client](https://github.com/websocket-client/websocket-client) | 千问流式语音识别连接 | Apache-2.0 |
| [synodriver/pysilk（silk-python）](https://github.com/synodriver/pysilk) | WAV与微信语音使用的SILK编码转换 | Python绑定和SILK编码器分别按对应发行包附带许可，不将两者视作本项目原创 |
| [FFmpeg](https://ffmpeg.org/) | 媒体解码、视频合成、字幕和音轨输出 | LGPL/GPL取决于实际构建；本仓库不随附FFmpeg可执行文件 |

上述Python依赖通过requirements.txt / requirements-voice.txt安装，不把虚拟环境或第三方Python二进制复制进源码仓库。

### Linux桌面运行组件

Dockerfile通过Ubuntu发行包安装以下组件，保留其各自许可证与包内版权声明，不将其源代码改署为本项目：

- [noVNC](https://github.com/novnc/noVNC)：浏览器访问远程桌面，核心MPL-2.0，其他素材按其[许可说明](https://github.com/novnc/noVNC/blob/master/LICENSE.txt)。
- [websockify](https://github.com/novnc/websockify)：WebSocket与VNC代理，LGPL-3.0；[x11vnc](https://github.com/LibVNC/x11vnc)：桌面VNC服务。
- [X.Org/Xvfb](https://www.x.org/)、[Openbox](https://openbox.org/)：虚拟显示与窗口管理。
- [PulseAudio](https://www.freedesktop.org/wiki/Software/PulseAudio/)：虚拟音频输入与语音发送。
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)：发送前群标题与界面状态的辅助校验，Apache-2.0；消息接收不是靠OCR逐条识别。
- [D-Bus](https://www.freedesktop.org/wiki/Software/dbus/)、[xdotool](https://github.com/jordansissel/xdotool)、[xclip](https://github.com/astrand/xclip)：桌面会话、输入与剪贴板协作。

### 外部服务与调研范围

MiniMax、阿里百炼、Fish Audio、OpenAI兼容服务，以及图库/题图所用的小小API、万站API、飞飞API，属于可选的**外部服务**，不是本仓库包含的开源模型或开源接口源码；按各自服务条款和账号权限使用。微信客户端由腾讯提供，不是本项目开源组件，仓库不分发微信核心DLL。

前期还调研过 [KOFKOY/weixin-ClawBot-API](https://github.com/KOFKOY/weixin-ClawBot-API)、[lich0821/WeChatFerry](https://github.com/lich0821/WeChatFerry)、[aixed/WeChat-Hook](https://github.com/aixed/WeChat-Hook) 和 [yyh-001/dsh-companion](https://github.com/yyh-001/dsh-companion)。**当前封存版未接入这些项目作为运行后端，也未安装DSH宿主/companion插件**，因此不将其列为直接依赖。

更详细的声明见[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。如发现遗漏的引用或权利声明，请通过Issue提供具体文件与来源，便于补充核对。
