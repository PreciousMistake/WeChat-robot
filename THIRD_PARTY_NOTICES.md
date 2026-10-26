# 第三方组件

根目录 `LICENSE` 的非商业使用条款仅适用于本项目著作权人有权授权的原创部分，不替代、修改或缩减下列第三方组件及依赖各自许可证授予的权利。原始版权声明、授权条件及免责声明继续适用。

原生桥接使用 MinHook 1.3.4，并保留构建所需的源码、头文件、作者信息及原始许可证：

- `native/vendor/minhook-1.3.4/LICENSE.txt`
- `native/vendor/minhook-1.3.4/AUTHORS.txt`
- `native/vendor/minhook-1.3.4/src/hde/` 下的原始版权声明

保留的 DLL 由本项目桥接源码和上述组件构建；不包含微信客户端程序或微信核心 DLL。

Python 依赖通过 `requirements.txt` 安装，不将它们的二进制复制进本仓库。表情缓存解密使用 cryptography，图片结构校验使用 Pillow，WXGF 动画转换使用 imageio-ffmpeg 所带的 FFmpeg；这些软件的许可证随各自发行包提供。

可选的 WAV 语音转码依赖通过 `requirements-voice.txt` 安装，使用 [silk-python / pysilk](https://github.com/synodriver/pysilk)；其 Python 绑定与 SILK 编码器的许可证随发行包提供。本仓库不复制编码器源码或二进制。


## 上游框架与人设

本修改版基于[Prometheus-first/helper](https://github.com/Prometheus-first/helper)，保留其非商业使用协议、原生桥接与组件版权。修改范围见CHANGELOG.md。

鲸鱼娘人设来自[linsport/dsh-whale-persona](https://github.com/linsport/dsh-whale-persona)，来源和版本见personas/README.md，原始许可见personas/whale-LICENSE.txt。MIT许可适用范围及其关于视觉形象、社区文本的排除说明继续保留。没有安装DSH或dsh-companion宿主。

## 格式与实现参考

native/voice_reader.py与wxbot_framework/voice_store.py头部保留对[fanyuantaier/wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica)的Apache-2.0布局/实现参考，以及[SQLCipher设计文档](https://www.zetetic.net/sqlcipher/design/)链接。未将整个wechatauto仓库作为运行依赖安装。

## 运行组件与外部服务

Python库、Linux桌面组件、媒体工具和外部服务的用途与许可证入口统一列在根README的“引用项目、来源与致谢”章节。Docker/apt/pip安装的第三方包仍以其实际发行包中的许可为准，不受根目录协议替换。云API、图库服务和腾讯微信客户端与开源库分开标注；仓库不随附其服务端源码、模型权重或微信核心DLL。
