<div align="center">
  <img src="assets/obs-monitor.svg" width="76" alt="OBS Monitor icon">

  <h1>OBS Monitor</h1>

  <p>
    <a href="README.md">简体中文</a> · <strong>English</strong>
  </p>

  <p><strong>A Windows recording console and app-free LAN mobile remote for OBS Studio</strong></p>
  <p>Track recording duration and real-time output bytes live, and start, pause, resume, or stop recordings from your phone by scanning a QR code.</p>

  <p>
    <a href="https://github.com/ElmaTony/obs-monitor/actions/workflows/ci.yml">
      <img src="https://github.com/ElmaTony/obs-monitor/actions/workflows/ci.yml/badge.svg" alt="CI Status">
    </a>
  </p>

  <p>
    <a href="#download"><strong>Download v2.3.3</strong></a> ·
    <a href="#interface-preview">Preview</a> ·
    <a href="#quick-start">Quick Start</a> ·
    <a href="#how-it-works-and-security">How It Works</a> ·
    <a href="#faq">FAQ</a> ·
    <a href="#development">Development</a> ·
    <a href="CHANGELOG.md">Changelog</a>
  </p>

  <br>

  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/desktop-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="assets/desktop-light.png">
    <img src="assets/desktop-light.png" width="840" alt="OBS Monitor desktop interface">
  </picture>

  <p><sub>Sample data shown · Automatically adapts to GitHub light (Warm Sand Coral) and dark (Warm Charcoal Coral outline) themes</sub></p>
</div>

---

## Why OBS Monitor

| Pain Point | How OBS Monitor Solves It |
| --- | --- |
| **Alt-tabbing breaks fullscreen recordings** | Keep your phone on your desk to monitor elapsed time and status live, with distinct desktop audio cues confirming every command without leaving your game or presentation. |
| **Explorer shows `0 KB` while recording** | Queries `GetRecordStatus.outputBytes` directly over OBS WebSocket v5, displaying both human-readable sizes and exact raw byte counts regardless of OS file metadata lag. |
| **Accidental double-taps or premature errors** | Controls strictly follow live OBS state; every command waits for encoder stability (at least 3 video frame intervals, plus up to 64 NVENC queued frames when stopping at fractional FPS) and never replays unknown requests. |
| **No mobile app install or cloud relay** | Bundled LAN web remote works right in your phone browser with zero external CDNs. Persistent pairing reconnects automatically across app switches, closed tabs, and desktop restarts. |

---

## Interface Preview

### Mobile Web Remote & Settings Dialog

<table>
  <tr>
    <td align="center" width="33%">
      <img src="assets/mobile-light.png" width="240" alt="Mobile light remote interface"><br>
      <sub>Mobile · Light (Warm Sand Coral)</sub>
    </td>
    <td align="center" width="33%">
      <img src="assets/mobile-dark.png" width="240" alt="Mobile dark remote interface"><br>
      <sub>Mobile · Dark (Warm Charcoal Coral)</sub>
    </td>
    <td align="center" width="34%">
      <img src="assets/settings-dark.png" width="270" alt="Desktop connection and alert settings dialog"><br>
      <sub>Desktop · Connection & Alerts</sub>
    </td>
  </tr>
</table>

<details>
<summary>Expand to compare Desktop Light & Dark themes side by side</summary>

<br>

<table>
  <tr>
    <td align="center" width="50%">
      <img src="assets/desktop-light.png" alt="Desktop light theme (Warm Sand Coral)"><br>
      <sub>Light · Warm Sand Coral</sub>
    </td>
    <td align="center" width="50%">
      <img src="assets/desktop-dark.png" alt="Desktop dark theme (Warm Charcoal Coral)"><br>
      <sub>Dark · Warm Charcoal Coral (outline cards)</sub>
    </td>
  </tr>
</table>

</details>

---

## Download

Current stable release: **v2.3.3** (all packages bundle the full runtime; no Python installation required).

| Platform | Installer (Recommended) | Portable (ZIP) | Single-File EXE |
| --- | --- | --- | --- |
| **Windows x64** (Intel / AMD) | [Download Setup](https://github.com/ElmaTony/obs-monitor/releases/download/v2.3.3/obs-monitor-2.3.3-windows-x64-setup.exe) | [Download ZIP](https://github.com/ElmaTony/obs-monitor/releases/download/v2.3.3/obs-monitor-2.3.3-windows-x64-portable.zip) | [Download EXE](https://github.com/ElmaTony/obs-monitor/releases/download/v2.3.3/obs-monitor-2.3.3-windows-x64.exe) |
| **Windows ARM64** (ARM PCs) | [Download Setup](https://github.com/ElmaTony/obs-monitor/releases/download/v2.3.3/obs-monitor-2.3.3-windows-arm64-setup.exe) | [Download ZIP](https://github.com/ElmaTony/obs-monitor/releases/download/v2.3.3/obs-monitor-2.3.3-windows-arm64-portable.zip) | [Download EXE](https://github.com/ElmaTony/obs-monitor/releases/download/v2.3.3/obs-monitor-2.3.3-windows-arm64.exe) |

- **Installer**: Creates Start Menu shortcuts and registers a standard uninstaller.
- **Portable**: Extract anywhere and run immediately—great for USB drives or zero-install setups.
- **Single-File EXE**: Standalone executable ready to launch without extraction.

[Complete Release Source](https://github.com/ElmaTony/obs-monitor/releases/download/v2.3.3/obs-monitor-2.3.3-source.zip) · [SHA-256 Checksums](https://github.com/ElmaTony/obs-monitor/releases/download/v2.3.3/SHA256SUMS.txt) · [All Releases](https://github.com/ElmaTony/obs-monitor/releases)

> **Requirements**: Windows 10 / 11 x64 or Windows 11 ARM64, and OBS Studio with **WebSocket v5** support (built into OBS 28+).

<details>
<summary>Verify SHA-256 checksums in PowerShell</summary>

```powershell
Get-FileHash .\obs-monitor-2.3.3-windows-x64-setup.exe -Algorithm SHA256
```

Compare the output hash against the corresponding line in [SHA256SUMS.txt](https://github.com/ElmaTony/obs-monitor/releases/download/v2.3.3/SHA256SUMS.txt).

</details>

---

## Quick Start

### 1. Enable WebSocket Server in OBS
Open OBS Studio, go to **Tools → WebSocket Server Settings**, check **Enable WebSocket server** (default port `4455`), and note your server password if authentication is enabled.

### 2. Launch the Desktop Console
Start OBS Monitor (a quick-start guide appears on first launch and can be reopened anytime via the top-right **?** button):
- OBS Monitor automatically detects local OBS WebSocket settings and connects;
- If connecting manually or targeting another OBS host on your LAN, open **Settings** in the top-right corner, enter the host, port, and password, then click **Save & Apply**;
- Once connected, control recordings with the four main buttons and manage master/per-action sound cues and previews on the right panel.

### 3. Pair Your Phone
1. Connect your phone and PC to the **same local network** (a wired PC and a phone on the same router's Wi-Fi work fine).
2. Scan the QR code on the right panel, or click **Copy Link** and open it in your phone's browser (default web port `8765`; switch LAN IPs from the dropdown if multiple adapters exist).
3. Bookmark the page or add it to your home screen after pairing—reopening the same URL in the same browser reconnects automatically.

---

## How It Works and Security

- **Live State Sync & Safety Locks**
  - Desktop polls OBS every ~`0.5 s` and mobile foreground pages refresh every ~`0.8 s` (polling pauses automatically in the background and syncs immediately upon return).
  - When disconnected or data is older than `3 s`, both UIs retain the last valid reading and disable recording buttons to prevent blind actions.
  - `GetRecordStatus.outputBytes` tracks cumulative encoder output; final MKV/MP4 files on disk may differ slightly due to container muxing overhead.
- **Audio Cues & Size Threshold Alerts**
  - Synthesizes five distinct PCM audio cues locally (start, pause, resume, stop, and size alert; corrupted cache files are automatically regenerated), with synced desktop/mobile master mute, per-action toggles, and live preview buttons.
  - Custom size threshold (`0.1`–`102400 GB`) triggers a one-time alert per recording; raising the threshold or disabling alerts mid-recording resets the alert state cleanly.
- **Persistent Pairing & Scoped Revocation**
  - Pairing persists via a `HttpOnly; SameSite=Strict` cookie and browser storage; background network discovery updates the QR code automatically and prefers phone-reachable LAN adapters over virtual interfaces.
  - Clear separation of revocation: **Forget This Device** (on phone) unpairs only the current browser, while **Reset All Phone Pairings** (in desktop settings) immediately revokes every prior link and browser session.
  - Re-pairing is required only when closing a private/incognito window, clearing site data, switching browsers, resetting all pairings from desktop settings, or changing the PC's LAN IP or remote web port.
- **Credential Isolation**
  - General preferences, theme choice, and first-run state are stored in `%APPDATA%/OBSAudioMonitor/config.json` (folder name kept for backward compatibility).
  - The OBS WebSocket password and mobile pairing secret are stored separately in **Windows Credential Manager**, never written in plaintext to disk or source; mobile API responses omit both OBS credentials and local recording file paths.
  - The mobile server uses HTTP for trusted LANs—never expose its port to the public internet, and share the pairing link only with trusted devices.

---

## FAQ

<details>
<summary><strong>My phone cannot open the remote page</strong></summary>

1. Verify that OBS Monitor is running on your PC and the mobile remote badge shows enabled.
2. Open the LAN address displayed by OBS Monitor (e.g. `192.168.x.x:8765`); `127.0.0.1` is reachable only on the PC itself.
3. Ensure Windows Firewall allows OBS Monitor on Private networks, and rule out VPNs, proxy clients, guest Wi-Fi, or router AP isolation.
4. Default ports: `4455` for OBS WebSocket and `8765` for the mobile web remote.

</details>

<details>
<summary><strong>Why are some recording buttons grayed out?</strong></summary>

Buttons strictly mirror live OBS state:
- **Idle**: Only **Start** is enabled.
- **Recording**: Only **Pause** and **Stop** are enabled.
- **Paused**: Only **Resume** and **Stop** are enabled.
- **Awaiting confirmation / Disconnected / Stale data**: Controls lock temporarily until OBS confirms the transition or connection recovers.

</details>

<details>
<summary><strong>OBS says pausing is not supported</strong></summary>

Open OBS **Settings → Output → Recording**. If your recording encoder is set to *Use stream encoder*, OBS cannot pause recordings. Switch to an independent recording encoder (e.g., NVENC, QuickSync, or x264) to enable pause and resume.

</details>

<details>
<summary><strong>Does closing OBS Monitor stop my active recording?</strong></summary>

No. Closing OBS Monitor only disconnects WebSocket and stops the local mobile web server. Any active OBS recording continues uninterrupted and can still be controlled directly in OBS Studio.

</details>

<details>
<summary><strong>How do I upgrade to a newer version?</strong></summary>

Exit the running OBS Monitor instance first:
- **Installer**: Run the new installer directly to upgrade in place.
- **Portable**: Extract the new archive to a fresh folder.
- **Single-File EXE**: Replace the existing `obs-monitor.exe`.

Your settings, OBS password, and mobile pairings for the current Windows user are preserved, and active OBS recordings are unaffected.

</details>

---

## Development

### Prerequisites
- Windows 10 / 11
- Python 3.12+
- Node.js 22+ (for mobile frontend and race-condition tests)

### Run from Source

```powershell
git clone https://github.com/ElmaTony/obs-monitor.git
cd obs-monitor
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

### Run Tests

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -v
node test_mobile.cjs
node test_mobile_races.cjs
node test_mobile_pairing.cjs
```

- **Automated Coverage**: 190 Python unit/UI tests and 49 mobile script tests covering recording controls, state confirmation, fractional-FPS encoder drain handling, raw output bytes, audio cues, settings validation, persistent pairing, reconnection, desktop UI regressions, and out-of-order request races.
- **CI & Release Verification**: Source code is verified on native Windows x64 and ARM64 runners via [GitHub Actions](https://github.com/ElmaTony/obs-monitor/actions); release packages undergo simulated OBS WebSocket v5 integration checks, single-instance checks, and silent install/uninstall smoke tests on both architectures.
- **Real-Hardware Baseline**: Verified on 2026-11-16 on Windows x64 with OBS Studio 33.0.1 (1080p60 / NVENC H.264 / MKV) and Android Chrome across all four controls, raw output byte parity, cookie-based reconnection, pairing persistence across restarts, and 1-frame-per-6-seconds stop drain.

### Build Release Packages
The complete release source archive (`obs-monitor-2.3.3-source.zip`) includes `BUILDING.md` and packaging scripts to build native x64 and ARM64 installers, portable archives, and single-file executables on Windows.

See [CHANGELOG.md](CHANGELOG.md), [CONTRIBUTING.md](CONTRIBUTING.md), and [SECURITY.md](SECURITY.md) for version history, contribution guidelines, and security reporting.

---

## License

Licensed under [GPL-3.0-only](LICENSE). Third-party notices and licenses are available in [NOTICE.txt](NOTICE.txt) and [licenses/](licenses/).

OBS Studio must be installed separately. OBS Monitor is an independent open-source project and is not affiliated with OBS Studio.
