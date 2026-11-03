"""Verify native release packages against an isolated OBS v5 protocol server."""
from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pairing import PairingIdentity
from platform_support import WindowsCredentialStore
from version import VERSION
from scripts.build_release import pe_arch
from websockets.sync.server import serve
from websockets.exceptions import ConnectionClosed


class SimulatedOBS:
    def __init__(self):
        self.active = self.paused = False
        self.bytes = self.duration = 0
        self.updated = time.monotonic()
        self.controls = []
        self.samples = set()
        self.lock = threading.Lock()

    def handler(self, connection):
        try:
            connection.send(json.dumps({'op':0,'d':{'rpcVersion':1,'obsWebSocketVersion':'5.6.0'}}))
            connection.recv()
            connection.send(json.dumps({'op':2,'d':{'negotiatedRpcVersion':1}}))
            for raw in connection:
                request = json.loads(raw)['d']
                with self.lock:
                    now = time.monotonic()
                    if self.active and not self.paused:
                        self.bytes += int((now-self.updated)*500000)
                        self.duration += int((now-self.updated)*1000)
                    self.updated = now
                    action, response = request['requestType'], {}
                    if action == 'GetVideoSettings':
                        response = {'fpsNumerator':30,'fpsDenominator':1}
                    elif action == 'GetRecordStatus':
                        response = {'outputActive':self.active,'outputPaused':self.paused,
                                    'outputBytes':self.bytes,'outputDuration':self.duration}
                        self.samples.add(self.bytes)
                    else:
                        self.controls.append(action)
                        if action == 'StartRecord':
                            self.active, self.paused, self.bytes, self.duration = True, False, 0, 0
                        elif action == 'StopRecord':
                            self.active, self.paused = False, False
                            response = {'outputPath':'test-recording.mkv'}
                        elif action == 'PauseRecord':
                            self.paused = True
                        elif action == 'ResumeRecord':
                            self.paused = False
                connection.send(json.dumps({'op':7,'d':{'requestId':request['requestId'],
                    'requestStatus':{'result':True,'code':100},'responseData':response}}))
        except ConnectionClosed:
            pass


def stop_owned_process(process):
    if process and process.poll() is None:
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        process.wait(timeout=15)


def wait_for_uninstall(directory, timeout=30):
    # Inno's bootstrap process can exit before its self-delete helper. ARM64
    # emulation makes that gap visible: the app is gone but unins000.exe is locked.
    deadline = time.monotonic()+timeout
    while any((directory/name).exists() for name in ('obs-monitor.exe','unins000.exe')):
        if time.monotonic() >= deadline:
            raise TimeoutError('Uninstaller did not finish releasing its own files')
        time.sleep(0.1)


def verify_program(executable, root):
    simulated = SimulatedOBS()
    server = serve(simulated.handler,'127.0.0.1',0)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1',0)); phone_port = reserve.getsockname()[1]
    profile = root / 'profile'
    data = profile / 'OBSAudioMonitor'
    data.mkdir(parents=True)
    (data / 'config.json').write_text(json.dumps({'obs_host':'127.0.0.1',
        'obs_port':server.socket.getsockname()[1], 'remote_port':phone_port,
        'sound_enabled':False,'alert_enabled':False}),encoding='utf-8')
    credential = WindowsCredentialStore(target=PairingIdentity.credential_target(data))
    environment = dict(os.environ,APPDATA=str(profile),QT_QPA_PLATFORM='offscreen')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    processes = []
    base = f'http://127.0.0.1:{phone_port}'
    browser = urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(CookieJar()))

    def api(path='/api/status', payload=None, token=None):
        headers = {'Content-Type':'application/json'}
        if token:
            headers['X-OBS-Token'] = token
        request = urllib.request.Request(base+path,headers=headers,
            data=json.dumps(payload).encode() if payload is not None else None)
        with browser.open(request,timeout=10) as response:
            if path == '/':
                return response.read().decode('utf-8')
            return json.load(response)

    def launch():
        process = subprocess.Popen([str(executable)],env=environment,startupinfo=startup)
        processes.append(process)
        return process

    def ready(process, token=None):
        deadline = time.monotonic()+35
        while time.monotonic()<deadline:
            if process.poll() is not None:
                log = data / 'startup-error.log'
                raise RuntimeError('Packaged app exited: '+(log.read_text(encoding='utf-8') if log.exists() else str(process.returncode)))
            try:
                value = api(token=token or credential.read())
                if value['connected']:
                    return value
            except (OSError,ValueError):
                pass
            time.sleep(0.15)
        raise TimeoutError('Packaged app did not become ready')

    try:
        first = launch()
        initial = ready(first)
        assert 'v'+VERSION in api('/'), 'Embedded phone page has the wrong version'
        # A fresh browser request carries only the persistent pairing cookie.
        assert api()['connected']
        for action in ('start','pause','resume','stop'):
            result = api('/api/control',{'action':action})
            assert result['ok']
            state = result['state']
            assert state['size_exact_text'] == f"{state['bytes']:,} 字节"
            assert state['bytes'] in simulated.samples
        assert simulated.controls == ['StartRecord','PauseRecord','ResumeRecord','StopRecord']
        duplicate = launch()
        duplicate.wait(timeout=15)
        assert duplicate.returncode == 0 and first.poll() is None
        old_identity = credential.read()
        stop_owned_process(first)
        restarted = launch()
        current = ready(restarted, token=old_identity)
        assert current['instance_id'] != initial['instance_id']
        assert credential.read() == old_identity
        assert api()['connected']
        return {'startup':True,'embedded_page_version':True,'cookie_revisit':True,
                'four_controls':True,'raw_output_bytes':True,'single_instance':True,
                'restart_keeps_pairing':True,'restart_changes_state_instance':True}
    finally:
        for process in processes:
            stop_owned_process(process)
        server.shutdown()
        credential.delete()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--arch',choices=('x64','arm64'),required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    prefix = f'obs-monitor-{VERSION}-windows-{args.arch}'
    report = {'version':VERSION,'architecture':args.arch,'protocol_server':'simulated OBS WebSocket v5','checks':{}}
    with tempfile.TemporaryDirectory(prefix='obs-monitor-verify-') as temporary:
        root = Path(temporary)
        onefile = directory / (prefix+'.exe')
        assert pe_arch(onefile) == args.arch
        report['checks']['onefile'] = verify_program(onefile,root/'onefile')
        with ZipFile(directory / (prefix+'-portable.zip')) as archive:
            assert archive.testzip() is None
            archive.extractall(root/'portable')
        portable = root/'portable'/'obs-monitor'/'obs-monitor.exe'
        assert pe_arch(portable) == args.arch
        report['checks']['portable'] = verify_program(portable,root/'portable-check')
        installer = directory / (prefix+'-setup.exe')
        installed = root/'installed'
        # Never replace an existing user's registered installation during QA.
        import winreg
        try:
            existing = winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B2F60A20-208F-45DF-9441-2B48FDE360D4}_is1')
        except FileNotFoundError:
            existing = None
        if existing:
            existing.Close()
            report['checks']['installer'] = {'status':'not_run','reason':'Existing installation preserved'}
        else:
            try:
                subprocess.run([str(installer),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/NOICONS',f'/DIR={installed}'],check=True,timeout=60)
                assert (installed/'obs-monitor.exe').is_file()
                report['checks']['installer'] = verify_program(installed/'obs-monitor.exe',root/'installer-check')
            finally:
                uninstaller = installed/'unins000.exe'
                if uninstaller.exists():
                    subprocess.run([str(uninstaller),'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],check=True,timeout=60)
                    wait_for_uninstall(installed)
                assert not (installed/'obs-monitor.exe').exists(), 'Uninstall did not remove its application'
            report['checks']['uninstall'] = True
    (directory / f'smoke-windows-{args.arch}.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    main()
