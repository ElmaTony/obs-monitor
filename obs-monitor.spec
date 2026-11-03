# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path
import PySide6
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct

project = Path(SPECPATH)
namespace = {}
exec((project / 'version.py').read_text(encoding='utf-8'), namespace)
version = namespace['VERSION']
mode = os.environ.get('OBS_MONITOR_BUNDLE', 'onefile')
assert mode in ('onefile', 'onedir')
data = [(str(project / name), name if (project / name).is_dir() else '.')
        for name in ('mobile.html', 'LICENSE', 'NOTICE.txt', 'licenses') if (project / name).exists()]
a = Analysis([str(project / 'main.py')], pathex=[str(project)], binaries=[], datas=data,
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['tkinter', 'numpy', 'PyQt5', 'PyQt6', 'PySide2'], noarchive=False, optimize=0)
# Use OS UCRT/ICU and Qt's matching VC runtime, rather than unrelated DLLs on PATH.
qt_root = Path(PySide6.__file__).parent
vc_runtime = [path for path in qt_root.iterdir() if path.suffix.lower() == '.dll'
              and path.name.lower().startswith(('msvcp140', 'vcruntime140', 'concrt140'))]
vc_names = {path.name.lower() for path in vc_runtime}
a.binaries = [entry for entry in a.binaries
              if not Path(entry[0]).name.lower().startswith(('api-ms-win-', 'ext-ms-win-'))
              and Path(entry[0]).name.lower() not in vc_names | {'ucrtbase.dll', 'icuuc.dll', 'icuin.dll', 'icudt.dll'}]
a.binaries += [(path.name, str(path), 'BINARY') for path in vc_runtime]
parts = tuple(int(part) for part in version.split('.')) + (0,)
version_info = VSVersionInfo(ffi=FixedFileInfo(filevers=parts, prodvers=parts,
    mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)),
    kids=[StringFileInfo([StringTable('040904B0', [StringStruct(key,value) for key,value in {
        'CompanyName':'obs-monitor contributors', 'FileDescription':'OBS Monitor',
        'FileVersion':version, 'ProductName':'OBS Monitor', 'ProductVersion':version,
        'OriginalFilename':'obs-monitor.exe', 'LegalCopyright':'GPL-3.0-only; see LICENSE'
    }.items()])]), VarFileInfo([VarStruct('Translation',[1033,1200])])])
pyz = PYZ(a.pure)
options = dict(name='obs-monitor', debug=False, bootloader_ignore_signals=False,
               strip=False, upx=False, console=False, disable_windowed_traceback=False,
               version=version_info)
icon = os.environ.get('OBS_MONITOR_ICON')
if icon:
    options['icon'] = icon
if mode == 'onedir':
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **options)
    collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='obs-monitor')
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **options)
