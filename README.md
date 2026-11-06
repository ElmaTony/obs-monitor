<p align="center">
  <img src="assets/obs-monitor.svg" width="72" alt="OBS Monitor 图标">
</p>

# OBS Monitor

在电脑或手机上控制 OBS 录制，实时查看录制时长和累计输出大小。

OBS Monitor 是一款 Windows 桌面工具。手机通过局域网网页连接，无需安装手机 App；首次配对后，可以随时关闭浏览器，下次访问自动恢复连接。

**[下载最新版](https://github.com/ElmaTony/obs-monitor/releases/latest)** · [更新记录](CHANGELOG.md) · [反馈问题](https://github.com/ElmaTony/obs-monitor/issues)

![OBS Monitor 桌面界面](assets/desktop-light.png)

*界面使用示例数据，支持浅色与深色主题。*

## 主要功能

| 功能 | 说明 |
| --- | --- |
| 录制控制 | 开始、结束、暂停、继续；电脑和手机状态同步 |
| 实时大小 | 直接读取 OBS 累计输出字节数，录像文件属性显示 0 KB 时仍可更新 |
| 手机遥控 | 扫码或粘贴链接配对，关闭浏览器后可重新连接 |
| 声音反馈 | 四种操作有不同提示音，可总关闭或分别关闭 |
| 大小提醒 | 达到设定阈值后提醒，每次录制提醒一次 |
| 连接恢复 | 断网自动重连，回到手机页面时立即更新状态 |
| 桌面界面 | 紧凑布局、双主题、单实例运行 |
| 首次指引 | 首次启动自动显示使用说明，之后可点右上角问号查看 |

## 下载

当前版本：**2.2.2**。所有程序包均包含运行环境，无需安装 Python。

| 平台 | 安装版 | 便携版 | 单文件 EXE |
| --- | --- | --- | --- |
| Windows x64 | [下载](https://github.com/ElmaTony/obs-monitor/releases/download/v2.2.2/obs-monitor-2.2.2-windows-x64-setup.exe) | [下载](https://github.com/ElmaTony/obs-monitor/releases/download/v2.2.2/obs-monitor-2.2.2-windows-x64-portable.zip) | [下载](https://github.com/ElmaTony/obs-monitor/releases/download/v2.2.2/obs-monitor-2.2.2-windows-x64.exe) |
| Windows ARM64 | [下载](https://github.com/ElmaTony/obs-monitor/releases/download/v2.2.2/obs-monitor-2.2.2-windows-arm64-setup.exe) | [下载](https://github.com/ElmaTony/obs-monitor/releases/download/v2.2.2/obs-monitor-2.2.2-windows-arm64-portable.zip) | [下载](https://github.com/ElmaTony/obs-monitor/releases/download/v2.2.2/obs-monitor-2.2.2-windows-arm64.exe) |

Intel / AMD 电脑选择 x64；Windows ARM 设备选择 ARM64。安装版提供快捷方式与卸载入口，便携版解压后运行，单文件 EXE 可直接运行。

[完整发行源码](https://github.com/ElmaTony/obs-monitor/releases/download/v2.2.2/obs-monitor-2.2.2-source.zip) · [SHA-256 校验清单](https://github.com/ElmaTony/obs-monitor/releases/download/v2.2.2/SHA256SUMS.txt) · [历史发行版本](https://github.com/ElmaTony/obs-monitor/releases)

运行要求：Windows 10 / 11 x64，或 Windows 11 ARM64，以及支持 **WebSocket v5** 的 OBS Studio。源码包用于查看源码、开发和构建，操作步骤见[开发说明](#开发)。

## 快速开始

### 电脑端

1. 打开 OBS，在 **工具 → WebSocket 服务器设置** 中启用服务器，默认端口为 `4455`。
2. 运行 OBS Monitor，首次启动会自动显示使用说明。工具会尝试读取本机 OBS 配置；未连接时，点击右上角 **设置**，填写与 OBS 一致的地址、端口和密码。
3. 连接成功后，用四个录制按钮控制 OBS。声音开关和各操作的试听按钮位于右侧。

<details>
<summary>查看设置界面</summary>

<p><img src="assets/settings-dark.png" width="470" alt="深色设置界面，同色标题栏与减号、加号调节按钮，使用示例设置"></p>

</details>

### 手机端

1. 手机与电脑连接同一局域网。电脑接网线、手机连接同一路由器的 Wi-Fi 也可以。
2. 扫描电脑工具中的二维码，或打开 **复制链接** 得到的完整地址。默认手机网页端口为 `8765`。
3. 首次配对成功后，可以将页面加入书签。之后在同一浏览器访问同一电脑地址，会自动恢复连接。

<details>
<summary>查看手机界面</summary>

<p><img src="assets/mobile-light.png" width="320" alt="手机端录制控制与配对状态，使用示例数据"></p>

<p><img src="assets/mobile-dark.png" width="320" alt="深色手机控制界面，使用示例数据"></p>

</details>

## 配对与重新连接

录制时可以切换其他 App、关闭标签页或退出浏览器，无需一直保持页面打开。电脑工具重启后也会保留配对；手机返回页面时会重新读取当前录制状态。

- **忘记此设备**：清除当前浏览器的配对。
- **重置所有手机配对**：在电脑设置中操作，所有旧链接和已配对浏览器都需要重新连接。
- **需要重新配对的情况**：无痕窗口关闭、清除网站数据、更换浏览器，或电脑端重置了配对。
- **电脑地址变化**：IP 或网页端口改变后，请使用工具显示的新链接。

手机遥控期间，电脑工具需要保持运行。控制链接包含遥控凭证，请只分享给可信设备，并在可信局域网中使用。

## 实时录制大小说明

录制中的文件可能暂时显示 **0 KB**。OBS Monitor 读取的是 OBS 的 `GetRecordStatus.outputBytes`，不依赖文件属性，也不按码率估算；两端同时显示原始字节数和便于阅读的 KB / MB / GB。

这个计数表示 **OBS 累计输出量**。最终 MKV / MP4 文件还包含封装信息，文件长度可能与计数不同。

桌面约每 0.5 秒更新，手机前台约每 0.8 秒更新。断线或数据过期时，页面会标记上次有效数据并暂时禁用录制按钮。操作成功以 OBS 的实际状态确认为准。

## 常见问题

### 手机打不开页面

确认电脑工具正在运行，并使用工具显示的局域网链接。`127.0.0.1` 仅供电脑本机访问。

检查 Windows 防火墙是否允许 OBS Monitor 通过专用网络，手机和电脑之间是否存在 VPN、访客 Wi-Fi 或设备隔离。OBS 的连接端口默认是 `4455`，手机网页端口默认是 `8765`。

### 为什么部分按钮是灰色的？

按钮随录制状态变化：准备就绪时可开始；录制中可暂停或结束；暂停后可继续或结束。正在处理操作、连接中断或数据过期时，需要等待最新状态。

### OBS 提示无法暂停

检查 OBS 的录制编码设置是否支持暂停。“与直播相同的编码器”等配置可能不支持，请使用支持暂停的独立录制编码器。

### 关闭工具会停止录制吗？

正在进行的录制会继续。关闭 OBS Monitor 会关闭它的连接和手机网页服务，之后仍可在 OBS 中操作录制。

### 如何升级？

升级前退出 OBS Monitor：安装版运行新版安装程序，便携版解压到新目录，单文件版替换原 EXE。同一 Windows 用户的设置和手机配对会保留，OBS 中正在进行的录制会继续。电脑 IP 或手机网页端口变化时，请使用工具显示的新链接。

### 设置和密码保存在哪里？

设置及使用说明的展示状态保存在 `%APPDATA%/OBSAudioMonitor/config.json`，沿用该目录以兼容已有配置。OBS 密码和手机配对凭证分别保存在 Windows 凭据管理器中；它们不会写入源码目录。

## 开发

<details>
<summary>源码运行与测试</summary>

源码运行使用 Windows 和 Python 3.12；手机脚本测试需要 Node.js 22 或更新版本。

```powershell
git clone https://github.com/ElmaTony/obs-monitor.git
cd obs-monitor
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -v
node test_mobile.cjs
node test_mobile_races.cjs
node test_mobile_pairing.cjs
```

测试覆盖录制控制、原始字节、声音、设置、持久配对、连接恢复和乱序响应。v2.2.2 已通过 Windows x64 与 ARM64 的[源码自动测试](https://github.com/ElmaTony/obs-monitor/actions/runs/42009205159)，以及两种架构发行程序对模拟 OBS 的[联调和安装卸载检查](https://github.com/ElmaTony/obs-monitor/actions/runs/42206526117)。手机脚本测试使用模拟浏览器环境；实际手机网络、防火墙和扬声器仍需在使用环境确认。

完整发行源码包包含构建脚本和 `BUILDING.md`，可在对应架构的 Windows 环境中构建。

贡献约定见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全说明见 [SECURITY.md](SECURITY.md)。

</details>

## 许可证

本项目以 [GPL-3.0-only](LICENSE) 发布，第三方组件遵循各自许可证，详见 [NOTICE.txt](NOTICE.txt) 与 [licenses/](licenses/)。

OBS Studio 需要单独安装。OBS Monitor 是独立项目，与 OBS Studio 无隶属关系。
