"""Build native Windows releases. Run once on x64 and once on ARM64."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from version import VERSION


def pe_arch(path):
    with Path(path).open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError(f'Not a Windows executable: {path}')
        stream.seek(0x3c)
        offset = struct.unpack('<I', stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b'PE\0\0':
            raise ValueError(f'Invalid PE file: {path}')
        machine = struct.unpack('<H', stream.read(2))[0]
    return {0x8664:'x64', 0xaa64:'arm64', 0x14c:'x86'}.get(machine, hex(machine))


def source_files():
    allowed_root = {'.gitignore','.gitattributes','README.md','CHANGELOG.md','LICENSE','NOTICE.txt',
                    'CONTRIBUTING.md','SECURITY.md','obs-monitor.spec'}
    for file in sorted(ROOT.rglob('*')):
        relative = file.relative_to(ROOT)
        if not file.is_file() or any(part in ('.git','__pycache__','node_modules','build','dist','release','work','.venv') for part in relative.parts):
            continue
        if len(relative.parts) == 1:
            if file.suffix in ('.py','.cjs') or file.name.startswith('requirements') or file.name in allowed_root:
                yield file
        elif relative.parts[0] in ('.github','scripts','packaging','assets','licenses') and file.suffix not in ('.pyc','.log','.exe','.zip'):
            yield file


def prepare_licenses():
    destination = ROOT / 'licenses'
    destination.mkdir(exist_ok=True)
    names = ['PySide6','PySide6_Essentials','PySide6_Addons','shiboken6','Pillow','qrcode','websocket-client','pyinstaller','pyinstaller-hooks-contrib']
    for name in names:
        distribution = importlib.metadata.distribution(name)
        for entry in distribution.files or []:
            parts = entry.parts
            if any(part.endswith('.dist-info') for part in parts) and ('licenses' in parts or entry.name.lower().startswith(('license','copying'))):
                index = next(i for i,p in enumerate(parts) if p.endswith('.dist-info'))
                relative = Path(*parts[index+1:])
                target = destination / name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(distribution.locate_file(entry), target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise FileNotFoundError('Python LICENSE.txt is required for distribution')
    shutil.copy2(python_license, destination / 'Python-LICENSE.txt')
    qt_licenses = Path(importlib.metadata.distribution('PySide6').locate_file('PySide6')) / 'Qt3rdPartyLicenses'
    if qt_licenses.is_dir():
        shutil.copytree(qt_licenses, destination / 'Qt-ThirdParty', dirs_exist_ok=True)


def run(command, **kwargs):
    subprocess.run([str(part) for part in command], check=True, cwd=ROOT, **kwargs)


def build_icon(work):
    from PIL import Image, ImageDraw
    image = Image.new('RGBA',(256,256),(0,0,0,0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8,8,248,248),radius=68,fill='#4263eb')
    draw.ellipse((85,85,171,171),fill='white')
    filename = work / 'obs-monitor.ico'
    image.save(filename, sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    return filename


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--arch', choices=('x64','arm64'), required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'release')
    parser.add_argument('--work', type=Path, default=ROOT / 'build' / 'release')
    parser.add_argument('--iscc', type=Path)
    parser.add_argument('--skip-tests', action='store_true', help='Only after the unchanged source has passed all checks')
    parser.add_argument('--no-source', action='store_true', help='The other matrix job produces the shared source archive')
    args = parser.parse_args()
    if sys.platform != 'win32' or pe_arch(sys.executable) != args.arch:
        parser.error('Build with a native Windows Python of the requested architecture')
    output, work = args.output.resolve(), args.work.resolve()
    output.mkdir(parents=True, exist_ok=True); work.mkdir(parents=True, exist_ok=True)
    iscc = str(args.iscc) if args.iscc else shutil.which('ISCC.exe')
    if not iscc:
        candidates = [Path(os.environ.get('ProgramFiles(x86)','')) / 'Inno Setup 6' / 'ISCC.exe',
                      Path(os.environ.get('ProgramFiles','')) / 'Inno Setup 7' / 'ISCC.exe']
        iscc = next((str(path) for path in candidates if path.is_file()), None)
    if not iscc or not Path(iscc).is_file():
        parser.error('Install Inno Setup or supply --iscc with its ISCC.exe path')
    if not args.skip_tests:
        environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONDONTWRITEBYTECODE='1')
        run([sys.executable,'-B','-m','unittest','discover','-v'], env=environment)
        for script in ('test_mobile.cjs','test_mobile_races.cjs','test_mobile_pairing.cjs'):
            run(['node',script])
    prepare_licenses()
    icon = build_icon(work)
    environment = dict(os.environ, OBS_MONITOR_ICON=str(icon), PYTHONDONTWRITEBYTECODE='1')
    for mode in ('onefile','onedir'):
        run([sys.executable,'-B','-m','PyInstaller','--noconfirm','--workpath',work / mode,
             '--distpath',work / 'dist' / mode,ROOT / 'obs-monitor.spec'],
            env=dict(environment, OBS_MONITOR_BUNDLE=mode))
    onefile = work / 'dist' / 'onefile' / 'obs-monitor.exe'
    portable = work / 'dist' / 'onedir' / 'obs-monitor'
    assert pe_arch(onefile) == pe_arch(portable / 'obs-monitor.exe') == args.arch
    # Architecture checks prevent a mislabeled emulated build from being published.
    for file in (portable / '_internal').rglob('*'):
        if file.suffix.lower() in ('.dll','.pyd'):
            actual = pe_arch(file)
            if actual != args.arch:
                raise RuntimeError(f'Wrong architecture in {file.name}: {actual}')
    for name in ('README.md','LICENSE','NOTICE.txt'):
        shutil.copy2(ROOT / name, portable / name)
    shutil.copytree(ROOT / 'licenses', portable / 'licenses', dirs_exist_ok=True)
    prefix = f'obs-monitor-{VERSION}-windows-{args.arch}'
    shutil.copy2(onefile, output / (prefix + '.exe'))
    with ZipFile(output / (prefix + '-portable.zip'),'w',ZIP_DEFLATED) as archive:
        for file in sorted(portable.rglob('*')):
            if file.is_file():
                archive.write(file,'obs-monitor/' + file.relative_to(portable).as_posix())
    run([iscc,f'/DAppVersion={VERSION}',f'/DTargetArch={args.arch}',f'/DSourceDir={portable}',
         f'/DOutputDir={output}', ROOT / 'packaging' / 'windows.iss'])
    source = output / f'obs-monitor-{VERSION}-source.zip'
    if not args.no_source:
        with ZipFile(source,'w',ZIP_DEFLATED) as archive:
            for file in source_files():
                archive.write(file,'obs-monitor/' + file.relative_to(ROOT).as_posix())
    packages = sorted(file for file in output.iterdir() if (file.name.startswith(prefix) or not args.no_source and file == source) and file.suffix in ('.exe','.zip'))
    for file in packages:
        if file.suffix == '.zip':
            with ZipFile(file) as archive:
                assert archive.testzip() is None
    hashes = {file.name:hashlib.sha256(file.read_bytes()).hexdigest() for file in packages}
    (output / f'SHA256SUMS-windows-{args.arch}.txt').write_text(''.join(f'{digest}  {name}\n' for name,digest in hashes.items()),encoding='ascii')
    (output / f'build-windows-{args.arch}.json').write_text(json.dumps({'version':VERSION,'architecture':args.arch,
        'python':sys.version.split()[0], 'checksums':hashes}, indent=2),encoding='utf-8')
    print(json.dumps({'version':VERSION,'architecture':args.arch,'packages':list(hashes)}), flush=True)


if __name__ == '__main__':
    main()
