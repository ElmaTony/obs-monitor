def main():
    try:
        from desktop_ui import main as launch
        launch()
    except Exception:
        import os
        import traceback
        from pathlib import Path
        directory = Path(os.environ.get("APPDATA", str(Path.home()))) / "OBSAudioMonitor"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        raise

if __name__ == "__main__":
    main()
