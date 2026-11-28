def main():
    try:
        from desktop_ui import main as launch
        launch()
    except Exception as exc:
        import os
        import sys
        import traceback
        from pathlib import Path
        directory = Path(os.environ.get("APPDATA") or Path.home()) / "OBSAudioMonitor"
        log_path = directory / "startup-error.log"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    f"OBS Monitor 启动失败：{exc}\n\n错误日志已写入：\n{log_path}",
                    "OBS Monitor",
                    0x10,
                )
            except Exception:
                pass
        raise

if __name__ == "__main__":
    main()
