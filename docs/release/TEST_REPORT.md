# v1.0.0 验证记录

日期：2026-10-26，Asia/Shanghai。

## 离线回归

- 从白名单干净导出目录、短路径 `D:/wcf` 运行 `python tools/run_tests.py`：**1094项测试，OK，1项跳过**，约52秒。
- 使用Python3.12虚拟环境，安装requirements及websocket-client/imageio-ffmpeg等语音和媒体依赖；FFmpeg可用。未连接真实微信，未请求真实云服务。
- `node --check webui/app.js`：通过。
- `node --test tests/webui_parity.test.mjs`：3项通过。
- Windows新增主人/生图配置、去重、停止/配置变更/会话保护、自然聊天热启停回归通过。
- Linux主人保存、真实sender身份、跨群与旧文字/语音失效测试通过。
- Linux干净源码共享voice_store导入及Dockerfile cryptography依赖声明测试通过。

先前开发环境全量运行遇到缺失websocket/FFmpeg和Windows长路径问题；安装已有声明依赖、在短路径干净目录重跑后通过，不以开发机失败结果作为封存通过结论。

## 真人与部署边界

Linux文字、多群隔离、语音收发、图库、成语接龙、看图游戏、成员绑定/总结/ID查询此前已完成真实群验收，用户确认生图成功。Windows新增主人和生图路径本次只完成离线验证，没有把Linux实测当作Windows真人验收。

好友自动通过不属于Linux已完成能力；进退群真人验收、记录清空验收、全新Docker镜像重建、整机恢复和长期运行稳定性仍未专项验收。未为封存测试重建当前已登录微信容器。

## 视频与发布

中文介绍视频：约4分41秒，1920×1080/24fps，H.264视频、AAC音轨；SRT字幕另附。真实管理台使用合成账号/群配置示范，真实群聊结果裁剪掉身份信息，并明确其中人工恢复样本的性质。

发布文件经白名单导出、凭据模式与本机已知密钥精确扫描，并以SHA256SUMS校验。仓库无个人运行配置、微信状态、数据库或服务器凭据。
