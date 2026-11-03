# OBS Monitor

Windows 上的 OBS 录制控制台，提供电脑按钮、手机局域网遥控、实时录制大小与可关闭的声音反馈。当前版本 **2.1.1**，支持浅色与深色主题。

## 下载与运行

从 [GitHub Releases](https://github.com/ElmaTony/obs-monitor/releases) 选择适合设备的包：

| 文件 | 用途 |
| --- | --- |
| `obs-monitor-2.1.1-windows-x64-setup.exe` | Windows x64 安装版，带快捷方式与卸载入口 |
| `obs-monitor-2.1.1-windows-x64-portable.zip` | x64 免安装包，解压整个目录后运行 `obs-monitor.exe` |
| `obs-monitor-2.1.1-windows-x64.exe` | x64 单文件程序，无需 Python |
| `obs-monitor-2.1.1-windows-arm64-*` | Windows ARM64 原生版本，对应上述三种形式 |
| `obs-monitor-2.1.1-source.zip` | 完整源码、测试、许可证和构建脚本 |
| `SHA256SUMS.txt` | 发布文件的 SHA-256 校验值 |

x64 面向 Windows 10 / 11，ARM64 面向 Windows 11 ARM。OBS Studio 需要另外安装，启用其内置的 **WebSocket v5** 服务。发布包不包含 OBS。

首次使用：

1. 在 OBS 的「工具 → WebSocket 服务器设置」启用服务，默认端口 `4455`。
2. 运行 OBS Monitor。工具会读取本机 OBS 连接配置；未连接时在右上角「设置」填写相同的地址、端口和密码。
3. 用电脑上的开始、暂停、继续、结束按钮控制录制。每种操作都有不同的电脑提示音，可总关闭或分别关闭。
4. 手机与电脑连接同一 Wi-Fi，扫描二维码，或打开电脑中「复制链接」得到的完整地址。默认手机端口 `8765`。

升级到 2.1.1 后，先关闭旧版工具，运行新版，再在手机重新加载页面，确认页脚为 `v2.1.1`。首次升级可能需要使用一次新版完整链接。

## 手机可以随时关闭浏览器

首次配对成功后，可以切换其他 App、关闭标签页或重启浏览器，之后访问同一电脑地址会自动恢复。电脑工具重启后也会保留配对，录制操作不会在重连时自动重放。

- 浏览器使用长期 HttpOnly Cookie 和本地存储保存配对。保留完整链接或加入书签，也可以恢复连接。
- 新浏览器支持粘贴完整链接配对，不必只能扫码。
- 「忘记此设备」只清除当前浏览器配对；电脑设置中的「重置所有手机配对」会使旧链接与所有旧配对失效。
- 无痕模式关闭、手动清除网站数据或更换浏览器后，需要使用完整链接重新配对。
- 访问地址由主机和端口组成。电脑 IP 或端口变化后需要打开新地址；建议给电脑设置稳定的局域网地址。
- 网页进入后台时停止轮询，恢复前台后立即重新读取状态；断网后自动重试，也可手动点「重新连接」。

电脑软件需要保持运行。首次访问若被防火墙拦截，请允许 OBS Monitor 的专用网络访问。访客 Wi-Fi 的设备隔离或 VPN 可能阻止连接，无需路由器端口映射。控制链接等同于遥控权限，请只给可信设备使用。

## 实时大小和录制控制

工具直接读取 OBS `GetRecordStatus.outputBytes`，显示原始整数「字节」和便于阅读的 KB / MB / GB。即使录像文件属性仍为 **0 KB**，数据也跟随 OBS 自身累计输出量更新，不通过文件属性或码率推算。

该数值精确对应 OBS 的累计输出统计；最终 MKV / MP4 还包含容器封装等信息，不能把它当成最终文件长度。桌面约 0.5 秒轮询，手机前台约 0.8 秒刷新。断线或数据超过 3 秒未更新时会标记旧数据并禁用操作。

控制请求只有在实际状态稳定后才报告成功和播放提示音。暂停、继续之间会按帧率等待至少三个视频帧间隔，且不少于 0.2 秒；操作失败、超时或结果未知时不会播放成功音，也不会自动重试控制命令。

OBS 的录制编码配置必须支持暂停。「与直播相同的编码器」等配置可能不支持，工具会显示 OBS 返回的错误。结束后保留上次大小与时长；开始新录制时重置。可设置大小提醒，每次录制提醒一次，断线重连不重复提醒。

关闭工具只会停止连接与手机服务，不会结束 OBS 中的录制。重复启动会激活已运行的窗口，避免重复声音与端口冲突。

## 界面和设置

桌面默认 880×600，最小 780×540；四个录制按钮同排，高 38 像素。电脑与手机独立记忆浅色 / 深色主题。

安装版和免安装版使用同一用户配置，保存在 `%APPDATA%/OBSAudioMonitor/config.json`，沿用旧版目录以兼容现有设置。OBS 密码与手机配对凭证分开保存在 Windows 凭据管理器中，不写入源码或普通 JSON。不随发布包分发个人配置、凭证、日志和录制文件。卸载程序保留用户设置。

## 开发与验证

需要 Windows、Python 3.12、Node.js 22 或更新版本：

```powershell
python -m pip install -r requirements-build.txt
python -B -m unittest discover -v
node test_mobile.cjs
node test_mobile_races.cjs
node test_mobile_pairing.cjs
python main.py
```

测试覆盖 OBS v5 协议、开始 / 暂停 / 继续 / 结束、状态确认、精确字节、声音、设置回滚、过期数据、HTTP 权限、浏览器持久配对、桌面重启、配对撤销、后台恢复和乱序响应。Qt 自动测试可设置 `QT_QPA_PLATFORM=offscreen`。

以前的真实 OBS 联调已验证文件属性 0 KB 时输出字节数仍增长。模拟测试与真实设备验收各有用途；实际手机 Wi-Fi、防火墙、扬声器音量仍需在使用环境确认。

## 本机打包

安装 [Inno Setup](https://jrsoftware.org/isinfo.php)，然后执行：

```powershell
python scripts/build_release.py --arch x64 --iscc "C:/Program Files/Inno Setup 7/ISCC.exe"
```

ARM64 必须在原生 Windows ARM64 Python 中执行同一脚本，参数改成 `--arch arm64`。脚本检查程序和所有 DLL / PYD 的 PE 架构，阻止把 x64 程序误标成 ARM64。完整流程包含测试、两种 PyInstaller 打包、安装包、源码与校验文件。输出默认放在忽略的 `release/`，临时文件在 `build/`，可用 `--output` / `--work` 指定其他目录。

仓库内 `.github/workflows/release.yml` 在 x64 与 `windows-11-arm` runner 分别原生构建。手动运行可下载构建产物；推送与 `version.py` 一致的 `v2.1.1` 标签会生成包含两种架构的 **草稿 Release**，检查后再发布。

依赖许可随包提供于 `licenses/`。主项目以 [GPL-3.0-only](LICENSE) 发布；第三方组件采用各自许可证，见 [NOTICE.txt](NOTICE.txt)。项目与 OBS Studio 无隶属关系。

协议参考：[OBS WebSocket 官方文档](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md)。
