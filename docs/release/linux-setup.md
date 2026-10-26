# Linux部署与启动

本版是已运行方案的源码封存，不是无人值守的一键安装包。已验证x86_64、官方Linux微信4.1.13.26、Ubuntu24.04桌面容器。宿主需要Docker/Compose、Python3.9以上与cryptography/Pillow。保留图形会话；发送依赖窗口和输入框状态。建议先在独立测试主机部署。

## 1. 专用目录与桌面

服务模板采用 `/opt/wechat-native-lab`。先由管理员准备：根目录root所有、组12000、权限710；`access` 为root所有、755；`home` 为专用UID/GID12000所有、700。UID12000不得属于其他业务。管理台服务使用同UID的宿主账号wechatlab。不要把已有业务目录当作此专用目录。

将干净源码完整放入 `access`。将 `deploy/linux-native-lab/` 中Dockerfile、compose.yaml、desktop.py复制到专用目录根；将wxbot_framework、linux_native、plugins、personas复制到 `home/native-access`，作为UID12000所有的非root工作副本。目录内不得含真实Windows配置或其他账号数据。

在专用目录执行：

```sh
cd /opt/wechat-native-lab
docker compose build
docker compose up -d
```

Dockerfile固定官方安装包SHA256；若官方链接更新导致校验失败，应先核对新版本兼容性，不直接删掉校验。当前镜像重建在新机器上的完整验收仍需自己执行。

## 2. 登录微信

桌面HTTP仅映射 `127.0.0.1:18086`。通过SSH转发：

```sh
ssh -L 18766:127.0.0.1:18086 user@your-server
```

浏览器打开 `http://127.0.0.1:18766/vnc.html?autoconnect=true&resize=scale`，由账号所有者扫码、手机确认。登录同一个小号可能让另一台桌面微信下线。确认 `home/xwechat_files/` 下该账号对应的目录，记录自己的account与storage；不要复制别人登录状态。

## 3. 初始化与服务

宿主配置Python依赖后，从源码根执行（示例账号和目录必须替换）：

```sh
cd /opt/wechat-native-lab/access
python3 -m pip install cryptography Pillow
PYTHONPATH=. python3 tools/init_linux_state.py --account wxid_your_account --storage wxid_your_account_suffix
```

由管理员将三个 `wechat-native-*.service` 安装到systemd，重载并启动wechat-native-receiver、wechat-native-ai和wechat-native-panel。服务模板使用上述固定目录和账号，改路径时应整体核对。初始化脚本不会覆盖已有状态，默认回复暂停、草稿开启、密钥为空。

接收监督进程以root定位本专用容器内已绑定账号的数据库密钥；密钥仅通过stdin传给容器非root工作进程，不写入配置。云模型与发送工作由非root进程执行。不要把代码放到其他群成员可写目录。

管理台在宿主回环地址18767：

```sh
ssh -L 18767:127.0.0.1:18767 user@your-server
```

打开 `http://127.0.0.1:18767`，刷新群列表并配置模型、人格、语音和插件。先保存测试群并做草稿验证，再开启真实发送。若新增群还未出现，等待接收服务更新联系人快照后刷新。

## 4. 维护

```sh
systemctl status wechat-native-receiver wechat-native-ai wechat-native-panel
journalctl -u wechat-native-ai -u wechat-native-receiver -n 50 --no-pager
```

不要直接公开数据库、日志、receiver.json或状态目录。先在管理台暂停回复再维护客户端。服务器/容器重启恢复、重新扫码、消息分片同名重建及跨多个分片发送需要专项验收，不在本版稳定性保证内。Linux清空记录、进退群真人验收、好友自动通过的边界见功能矩阵。
