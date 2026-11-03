import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import MagicMock, patch

import obs_controller as core
import settings
from remote_server import RemoteServer


class FakeSocket:
    def __init__(self, result=True):
        self.sent = []
        self.handshake = 0
        self.result = result
        self.closed = False

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self):
        self.handshake += 1
        if self.handshake == 1:
            return json.dumps({"op": 0, "d": {"rpcVersion": 1, "obsWebSocketVersion": "5.6.0", "authentication": {"salt": "salt", "challenge": "challenge"}}})
        if self.handshake == 2:
            return json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}})
        request = self.sent[-1]["d"]
        return json.dumps({"op": 7, "d": {"requestId": request["requestId"], "requestStatus": {"result": self.result, "code": 100 if self.result else 500, "comment": "recording unavailable"}, "responseData": {"outputActive": True, "outputPaused": False, "outputBytes": 3456, "outputDuration": 1000}}})

    def close(self):
        self.closed = True


class ProtocolTests(unittest.TestCase):
    def test_frame_interval_uses_fractional_video_rate(self):
        client = core.OBSWebSocketClient("127.0.0.1", 4455, "")
        with patch.object(client, "request", return_value={"fpsNumerator": 30000, "fpsDenominator": 1001}):
            self.assertAlmostEqual(client.get_frame_interval(), 1001 / 30000)

    def test_invalid_frame_rate_is_not_used_for_confirmation(self):
        client = core.OBSWebSocketClient("127.0.0.1", 4455, "")
        for rate in (0, -1, True, None, 29.97):
            with self.subTest(rate=rate), patch.object(client, "request", return_value={"fpsNumerator": rate, "fpsDenominator": 1}):
                with self.assertRaisesRegex(ValueError, "帧率"):
                    client.get_frame_interval()

    def test_missing_or_invalid_byte_counter_never_becomes_zero(self):
        client = core.OBSWebSocketClient("127.0.0.1", 4455, "")
        valid = {"outputActive": True, "outputPaused": False, "outputBytes": 0, "outputDuration": 1000}
        for value in (None, -1, 1.5, True, "12345"):
            with self.subTest(value=value), patch.object(client, "request", return_value={**valid, "outputBytes": value}):
                with self.assertRaisesRegex(ValueError, "outputBytes"):
                    client.get_record_status()
        with patch.object(client, "request", return_value={key: value for key, value in valid.items() if key != "outputBytes"}):
            with self.assertRaises(ValueError):
                client.get_record_status()

    def test_actual_zero_byte_counter_is_valid(self):
        client = core.OBSWebSocketClient("127.0.0.1", 4455, "")
        data = {"outputActive": True, "outputPaused": False, "outputBytes": 0, "outputDuration": 0}
        with patch.object(client, "request", return_value=data):
            self.assertEqual(client.get_record_status()["outputBytes"], 0)

    def test_real_protocol_request_status_and_hello_version(self):
        socket = FakeSocket()
        client = core.OBSWebSocketClient("127.0.0.1", 4455, "secret", socket_factory=lambda *a, **k: socket)
        self.assertEqual(client.connect()["obsWebSocketVersion"], "5.6.0")
        self.assertEqual(client.get_record_status()["outputBytes"], 3456)
        self.assertEqual(socket.sent[1]["d"]["requestType"], "GetRecordStatus")
        self.assertEqual(socket.sent[0]["d"]["authentication"], core.OBSWebSocketClient.build_authentication("secret", "salt", "challenge"))

    def test_obs_failure_is_not_success(self):
        socket = FakeSocket(result=False)
        client = core.OBSWebSocketClient("127.0.0.1", 4455, "secret", socket_factory=lambda *a, **k: socket)
        client.connect()
        with self.assertRaisesRegex(core.OBSRequestError, "unavailable"):
            client.request("StartRecord")

    def test_actual_websocket_server_all_four_commands(self):
        from websockets.sync.server import serve
        received = []

        def handler(connection):
            connection.send(json.dumps({"op": 0, "d": {"rpcVersion": 1, "obsWebSocketVersion": "5.6.0"}}))
            json.loads(connection.recv())
            connection.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
            for raw in connection:
                request = json.loads(raw)["d"]
                received.append(request["requestType"])
                connection.send(json.dumps({"op": 7, "d": {"requestId": request["requestId"], "requestStatus": {"result": True, "code": 100}, "responseData": {"outputBytes": 100000}}}))

        server = serve(handler, "127.0.0.1", 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = core.OBSWebSocketClient("127.0.0.1", server.socket.getsockname()[1], "")
            client.connect()
            for request in ("GetRecordStatus", "StartRecord", "PauseRecord", "ResumeRecord", "StopRecord"):
                client.request(request)
            client.close()
        finally:
            server.shutdown()
            worker.join(timeout=2)
        self.assertEqual(received, ["GetRecordStatus", "StartRecord", "PauseRecord", "ResumeRecord", "StopRecord"])


class FakeOBS:
    def __init__(self):
        self.active = False
        self.paused = False
        self.bytes = 0
        self.requests = []
        self.fail = False

    def connect(self):
        return {}

    def get_frame_interval(self):
        return 1 / 30

    def get_record_status(self):
        return {"outputActive": self.active, "outputPaused": self.paused, "outputBytes": self.bytes, "outputDuration": 1000 if self.active else 0}

    def request(self, action):
        self.requests.append(action)
        if self.fail:
            raise core.OBSRequestError("OBS refused")
        if action == "StartRecord":
            self.active = True
        elif action == "StopRecord":
            self.active, self.paused = False, False
        elif action == "PauseRecord":
            self.paused = True
        elif action == "ResumeRecord":
            self.paused = False
        return {"outputPath": "D:/recording.mkv"}

    def close(self):
        pass


class ControllerTests(unittest.TestCase):
    def test_rapid_controls_wait_for_encoder_pause_timestamps_to_clear(self):
        original = self.fake.request
        resumed_at = [None]
        def encoder_guard(request):
            if request == "PauseRecord" and resumed_at[0] is not None and time.monotonic() - resumed_at[0] < 0.15:
                return {}  # OBS acknowledges the request but cannot pause yet.
            response = original(request)
            if request == "ResumeRecord":
                resumed_at[0] = time.monotonic()
            return response
        self.fake.request = encoder_guard
        self.connected()
        for action in ["start"] + [action for _ in range(12) for action in ("pause", "resume")] + ["stop"]:
            self.assertTrue(self.controller.submit(action).result(timeout=2)["ok"])
        self.assertEqual(len([kind for kind, _ in self.events if kind == "action"]), 26)

    def test_low_fps_output_extends_transition_settling_window(self):
        self.fake.get_frame_interval = lambda: 1 / 5
        self.connected()
        started = time.monotonic()
        self.controller.submit("start").result(timeout=2)
        self.assertGreaterEqual(time.monotonic() - started, 0.6)

    def test_transient_target_state_does_not_report_success(self):
        self.controller.confirmation_timeout = 0.4
        self.controller._client = self.fake
        self.fake.request = lambda request: {}
        statuses = iter([False, True, False])
        self.fake.get_record_status = lambda: {"outputActive": next(statuses, False)}
        future = Future()
        self.controller._execute("start", future, time.monotonic())
        with self.assertRaisesRegex(core.OBSRequestError, "状态未改变"):
            future.result()
        self.assertFalse(any(kind == "action" for kind, _ in self.events))

    def test_concurrent_snapshots_have_unique_increasing_revisions(self):
        from concurrent.futures import ThreadPoolExecutor
        self.controller._observe({"outputActive": True, "outputBytes": 1234})
        with ThreadPoolExecutor(max_workers=8) as pool:
            revisions = list(pool.map(lambda _: self.controller.snapshot()["revision"], range(200)))
        self.assertEqual(len(set(revisions)), 200)
        self.assertGreater(self.controller.snapshot()["revision"], max(revisions))

    def test_pause_clock_adjustment_is_not_a_new_recording(self):
        self.controller._observe({"outputActive": True, "outputBytes": 200*1024**2, "outputDuration": 10000})
        self.controller._observe({"outputActive": True, "outputPaused": True, "outputBytes": 201*1024**2, "outputDuration": 9998})
        self.assertEqual([value for kind,value in self.events if kind == "action"], ["pause"])
        self.assertEqual(len([kind for kind,_ in self.events if kind == "size_alert"]), 1)

    def test_byte_counter_reset_without_time_reset_does_not_repeat_session_alert(self):
        self.controller._observe({"outputActive": True, "outputBytes": 300*1024**2, "outputDuration": 10000})
        self.controller._observe({"outputActive": True, "outputBytes": 200*1024**2, "outputDuration": 11000})
        self.assertEqual([kind for kind,_ in self.events if kind == "action"], [])
        self.assertEqual(len([kind for kind,_ in self.events if kind == "size_alert"]), 1)

    def test_connection_settings_keep_live_phone_sound_changes(self):
        candidate = settings.AppConfig(obs_port=4567, sound_enabled=True)
        with patch.object(self.config, "save"):
            self.controller.set_sound_enabled(False)
        with patch.object(candidate, "save") as save:
            self.controller.apply_connection_config(candidate)
            save.assert_called_once_with(save_password=True)
        self.assertFalse(self.controller.config.sound_enabled)
        self.assertEqual(self.controller.config.obs_port, 4567)

    def test_connection_settings_failure_preserves_current_config(self):
        candidate = settings.AppConfig(obs_port=4567)
        with patch.object(candidate, "save", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.controller.apply_connection_config(candidate)
        self.assertIs(self.controller.config, self.config)

    def test_realtime_size_ignores_zero_kb_placeholder_and_filesystem_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            placeholder = Path(tmp) / "still_recording.mkv"
            placeholder.write_bytes(b"")
            self.assertEqual(placeholder.stat().st_size, 0)
            with patch.object(Path, "stat", side_effect=AssertionError("Filesystem metadata must not be used for live OBS size")):
                for value in (1048576, 2097152, 3145728):
                    self.controller._observe({"outputActive": True, "outputBytes": value, "outputDuration": value // 1000})
                    self.assertEqual(self.controller.snapshot()["bytes"], value)
                    self.assertEqual(self.controller.snapshot()["size_exact_text"], f"{value:,} 字节")
            self.assertEqual(placeholder.stat().st_size, 0)

    def test_exact_size_text_preserves_bytes_hidden_by_unit_rounding(self):
        self.controller._observe({"outputActive": True, "outputBytes": 1048576})
        first = self.controller.snapshot()
        self.controller._observe({"outputActive": True, "outputBytes": 1048577})
        second = self.controller.snapshot()
        self.assertEqual(first["size_text"], second["size_text"])
        self.assertNotEqual(first["size_exact_text"], second["size_exact_text"])

    def test_exact_size_text_never_rounds_large_integer(self):
        value = 2**53 + 1
        self.controller._observe({"outputActive": True, "outputBytes": value})
        self.assertEqual(self.controller.snapshot()["size_exact_text"], "9,007,199,254,740,993 字节")

    def test_stale_sample_is_retained_and_controls_are_disabled(self):
        self.controller._observe({"outputActive": True, "outputBytes": 123456})
        self.controller._sampled_at = time.monotonic() - 3.2
        stale = self.controller.snapshot()
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["bytes"], 123456)
        self.assertFalse(any(stale["allowed"].values()))
        self.controller._observe({"outputActive": True, "outputBytes": 123457})
        self.assertFalse(self.controller.snapshot()["stale"])

    def setUp(self):
        self.events = []
        self.config = settings.AppConfig(threshold_gb=0.1)
        self.fake = FakeOBS()
        self.controller = core.OBSController(self.config, lambda k, p: self.events.append((k, p)), client_factory=lambda *a: self.fake, interval=0.01)

    def tearDown(self):
        self.controller.stop()

    def connected(self):
        self.controller.start()
        until = time.monotonic() + 2
        while not self.controller.snapshot()["connected"] and time.monotonic() < until:
            time.sleep(0.01)
        self.assertTrue(self.controller.snapshot()["connected"])

    def test_full_control_cycle_and_one_sound_event_each(self):
        self.connected()
        for action in ("start", "pause", "resume", "stop"):
            self.assertTrue(self.controller.submit(action).result(timeout=2)["ok"])
        self.assertEqual(self.fake.requests, ["StartRecord", "PauseRecord", "ResumeRecord", "StopRecord"])
        self.assertEqual([p for k, p in self.events if k == "action"], ["start", "pause", "resume", "stop"])
        self.assertEqual(self.controller.snapshot()["last_file"], "D:/recording.mkv")

    def test_failed_operation_does_not_emit_sound(self):
        self.connected()
        self.fake.fail = True
        with self.assertRaises(core.OBSRequestError):
            self.controller.submit("start").result(timeout=2)
        self.assertFalse(any(k == "action" for k, _ in self.events))

    def test_acknowledged_request_without_state_change_is_not_success(self):
        self.controller.confirmation_timeout = 0.05
        self.fake.request = lambda request: {}
        self.connected()
        with self.assertRaisesRegex(core.OBSRequestError, "状态未改变"):
            self.controller.submit("start").result(timeout=2)
        self.assertFalse(any(kind == "action" for kind, _ in self.events))

    def test_duplicate_start_is_rejected_without_second_request(self):
        self.connected()
        self.controller.submit("start").result(timeout=2)
        with self.assertRaises(core.OBSRequestError):
            self.controller.submit("start").result(timeout=2)
        self.assertEqual(self.fake.requests, ["StartRecord"])

    def test_disconnected_operation_is_not_queued_for_later(self):
        with self.assertRaises(ConnectionError):
            self.controller.submit("start").result()
        self.assertEqual(self.controller._commands.qsize(), 0)

    def test_size_alert_survives_reconnect_and_resets_for_new_recording(self):
        monitor = self.controller
        active = {"outputActive": True, "outputBytes": 200 * 1024**2, "outputDuration": 2000}
        monitor._observe(active)
        monitor._update(connected=False)
        monitor._observe({**active, "outputBytes": 210 * 1024**2, "outputDuration": 2200})
        self.assertEqual(len([k for k, _ in self.events if k == "size_alert"]), 1)
        monitor._observe({"outputActive": False})
        monitor._observe(active)
        self.assertEqual(len([k for k, _ in self.events if k == "size_alert"]), 2)

    def test_finish_preserves_final_size_until_next_recording(self):
        self.controller._observe({"outputActive": True, "outputBytes": 5000, "outputDuration": 10000})
        self.controller._observe({"outputActive": False})
        self.controller._observe({"outputActive": False})
        self.assertEqual(self.controller.snapshot()["bytes"], 5000)
        self.assertEqual(self.controller.snapshot()["duration_text"], "00:00:10")
        self.controller._observe({"outputActive": True, "outputBytes": 0, "outputDuration": 0})
        self.assertEqual(self.controller.snapshot()["bytes"], 0)

    def test_delayed_obs_state_does_not_play_acknowledged_sound_twice(self):
        self.controller._client = self.fake
        self.fake.request = lambda request: {}
        statuses = iter([{"outputActive": False}, {"outputActive": False}, {"outputActive": False}, {"outputActive": True}])
        self.fake.get_record_status = lambda: next(statuses, {"outputActive": True})
        future = Future()
        self.controller._execute("start", future, time.monotonic())
        self.assertTrue(future.result()["state"]["active"])
        self.assertEqual([value for kind, value in self.events if kind == "action"], ["start"])

    def test_expired_command_never_reaches_obs(self):
        self.controller._client = self.fake
        future = Future()
        self.controller._execute("start", future, time.monotonic() - 10)
        with self.assertRaises(core.OBSRequestError):
            future.result()
        self.assertEqual(self.fake.requests, [])

    def test_connection_loss_does_not_report_success_or_replay_command(self):
        self.connected()
        def lose_connection(request):
            self.fake.requests.append(request)
            raise ConnectionError("connection lost before acknowledgement")
        self.fake.request = lose_connection
        with self.assertRaisesRegex(ConnectionError, "结果未知"):
            self.controller.submit("start").result(timeout=2)
        self.assertFalse(any(kind == "action" for kind, _ in self.events))
        self.assertEqual(self.fake.requests, ["StartRecord"])

    def test_cancelled_command_does_not_reach_obs(self):
        self.controller._client = self.fake
        future = Future()
        future.cancel()
        self.controller._execute("start", future, time.monotonic())
        self.assertEqual(self.fake.requests, [])

    def test_sound_setting_rolls_back_on_save_failure(self):
        with patch.object(self.config, "save", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.controller.set_sound_enabled(False)
        self.assertTrue(self.config.sound_enabled)


class SettingsTests(unittest.TestCase):
    def test_json_replace_failure_restores_previous_password_and_json(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "CONFIG_PATH", Path(tmp)/"config.json"), patch.object(settings, "WindowsCredentialStore") as store:
            previous = '{"obs_port":4455}'
            settings.CONFIG_PATH.write_text(previous)
            store.return_value.read.return_value = "old-test-password"
            with patch.object(Path, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    settings.AppConfig(obs_password="new-test-password").save(save_password=True)
            self.assertEqual([call.args[0] for call in store.return_value.write.call_args_list], ["new-test-password", "old-test-password"])
            self.assertEqual(settings.CONFIG_PATH.read_text(), previous)
            self.assertFalse(settings.CONFIG_PATH.with_suffix('.tmp').exists())

    def test_json_prepare_failure_does_not_touch_password(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "CONFIG_PATH", Path(tmp)/"config.json"), patch.object(settings, "WindowsCredentialStore") as store:
            with patch.object(Path, "write_text", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    settings.AppConfig(obs_password="new-test-password").save(save_password=True)
            store.return_value.write.assert_not_called()

    def test_save_restore_and_no_password_in_json(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "CONFIG_PATH", Path(tmp) / "config.json"), patch.object(settings, "detect_obs_websocket_settings", return_value=("127.0.0.1", 4455, "")), patch.object(settings, "WindowsCredentialStore") as store:
            store.return_value.read.return_value = "secret"
            config = settings.AppConfig(obs_password="secret", sound_enabled=False, threshold_gb=5.5, remote_port=8888, theme="dark")
            config.sound_actions["pause"] = False
            config.save()
            raw = settings.CONFIG_PATH.read_text()
            self.assertNotIn("secret", raw)
            loaded = settings.AppConfig.load()
            self.assertFalse(loaded.sound_enabled)
            self.assertFalse(loaded.sound_actions["pause"])
            self.assertEqual(loaded.remote_port, 8888)
            self.assertEqual(loaded.threshold_gb, 5.5)
            self.assertEqual(loaded.theme, "dark")

    def test_legacy_hotkeys_are_preserved_and_normalized(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "CONFIG_PATH", Path(tmp) / "config.json"):
            settings.CONFIG_PATH.write_text(json.dumps({"start": "Ctrl+F9", "pause_hotkey": "Ctrl+F10"}))
            settings.AppConfig().save()
            saved = json.loads(settings.CONFIG_PATH.read_text())
            self.assertEqual(saved["start_hotkey"], "Ctrl+F9")
            self.assertEqual(saved["pause_hotkey"], "Ctrl+F10")

    def test_malformed_config_uses_defaults(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "CONFIG_PATH", Path(tmp) / "config.json"), patch.object(settings, "detect_obs_websocket_settings", return_value=("127.0.0.1", 4455, "")), patch.object(settings, "WindowsCredentialStore") as store:
            store.return_value.read.return_value = ""
            settings.CONFIG_PATH.write_text("[]")
            self.assertEqual(settings.AppConfig.load().remote_port, 8765)

    def test_phone_address_uses_lan_instead_of_vpn(self):
        from remote_server import lan_addresses
        with patch("remote_server.socket.socket") as sock, patch("remote_server.socket.gethostbyname_ex", return_value=("pc", [], ["198.18.0.1", "192.168.1.2"])):
            sock.return_value.__enter__.return_value.getsockname.return_value = ("198.18.0.1", 12345)
            self.assertEqual(lan_addresses(), ["192.168.1.2"])


class DesktopTests(unittest.TestCase):
    def test_sound_save_failure_restores_visible_switch(self):
        with patch.object(self.config, "save", side_effect=OSError("save failed")):
            self.app.sound_switch.setChecked(False)
        self.app._tick()
        self.assertTrue(self.config.sound_enabled)
        self.assertTrue(self.app.sound_switch.isChecked())

    def test_sound_action_ui_saves_through_shared_controller(self):
        with patch.object(self.config, "save") as save:
            self.app.sound_vars["pause"].click()
            save.assert_called_once()
        self.assertFalse(self.controller_config_sound("pause"))

    def controller_config_sound(self, action):
        return self.app.controller.config.sound_actions[action]

    def setUp(self):
        from PySide6.QtWidgets import QApplication
        from desktop_ui import MonitorApp
        self.qt = QApplication.instance() or QApplication([])
        self.qt.setStyle("Fusion")
        self.config = settings.AppConfig()
        self.player = MagicMock()
        self.app = MonitorApp(config=self.config, start_services=False, player=self.player)

    def tearDown(self):
        self.app.close()

    def test_ui_initializes_and_disconnected_controls_disabled(self):
        self.app._tick()
        self.assertTrue(all(not b.isEnabled() for b in self.app.action_buttons.values()))

    def test_desktop_controls_follow_recording_and_pause_state(self):
        self.app.controller._observe({"outputActive": True, "outputPaused": False})
        self.app._tick()
        self.assertTrue(self.app.action_buttons["pause"].isEnabled())
        self.assertFalse(self.app.action_buttons["resume"].isEnabled())
        self.app.controller._observe({"outputActive": True, "outputPaused": True})
        self.app._tick()
        self.assertFalse(self.app.action_buttons["pause"].isEnabled())
        self.assertTrue(self.app.action_buttons["resume"].isEnabled())

    def test_all_four_sounds_can_be_muted(self):
        self.config.sound_enabled = False
        for action in core.REQUESTS:
            self.app._on_event("action", action)
        self.app._tick()
        self.player.play.assert_not_called()

    def test_individual_sound_toggle(self):
        self.config.sound_actions["pause"] = False
        for action in ("start", "stop", "pause", "resume"):
            self.app._on_event("action", action)
        self.app._tick()
        self.assertEqual([call.args[0] for call in self.player.play.call_args_list], ["start", "stop", "resume"])

    def test_light_dark_theme_switch_preserves_recording_state(self):
        self.app.controller._observe({"outputActive": True, "outputBytes": 123456})
        before = self.app.controller.snapshot()
        with patch.object(self.config, "save") as save:
            self.app.apply_theme("dark")
            self.assertTrue(self.app.theme_buttons["dark"].isChecked())
            self.assertEqual(self.config.theme, "dark")
            self.app.apply_theme("light")
            self.assertTrue(self.app.theme_buttons["light"].isChecked())
            self.assertEqual(save.call_count, 2)
        after = self.app.controller.snapshot()
        self.assertEqual({k: before[k] for k in ("active", "paused", "bytes", "duration_ms")},
                         {k: after[k] for k in ("active", "paused", "bytes", "duration_ms")})

    def test_exact_byte_count_is_displayed_on_desktop(self):
        self.app.controller._observe({"outputActive": True, "outputBytes": 123456789})
        self.app._tick()
        self.assertEqual(self.app.size_exact_label.text(), "123,456,789 字节")


class AudioTests(unittest.TestCase):
    def test_all_four_operation_tones_are_distinct_valid_pcm(self):
        import hashlib
        import wave
        from platform_support import AudioPlayer
        hashes = set()
        with tempfile.TemporaryDirectory() as tmp:
            for action in ("start", "stop", "pause", "resume"):
                path = Path(tmp) / (action + ".wav")
                AudioPlayer._write_tone(path, AudioPlayer.SOUND_RECIPES[action])
                with wave.open(str(path), "rb") as sound:
                    self.assertEqual(sound.getnchannels(), 1)
                    self.assertEqual(sound.getsampwidth(), 2)
                    self.assertEqual(sound.getframerate(), 44100)
                    frames = sound.readframes(sound.getnframes())
                    self.assertTrue(any(frames))
                hashes.add(hashlib.sha256(path.read_bytes()).digest())
        self.assertEqual(len(hashes), 4)


class RemoteTests(unittest.TestCase):
    def test_cancelled_phone_connection_does_not_raise_handler_error(self):
        from http.client import RemoteDisconnected
        from http.server import BaseHTTPRequestHandler
        with patch.object(self.server.server, "handle_error") as handle_error:
            with patch.object(BaseHTTPRequestHandler, "end_headers", side_effect=ConnectionResetError("phone cancelled request")):
                with self.assertRaises(RemoteDisconnected):
                    self.request("/api/status")
            with self.request("/api/status") as response:
                self.assertTrue(json.load(response)["connected"])
            handle_error.assert_not_called()

    def setUp(self):
        self.fake = FakeOBS()
        self.config = settings.AppConfig()
        self.controller = core.OBSController(self.config, client_factory=lambda *a: self.fake, interval=0.01)
        self.controller.start()
        until = time.monotonic() + 2
        while not self.controller.snapshot()["connected"] and time.monotonic() < until:
            time.sleep(0.01)
        self.server = RemoteServer(self.controller, host="127.0.0.1", port=0)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.stop()
        self.controller.stop()

    def request(self, path, data=None, token=True, origin=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-OBS-Token"] = self.server.token
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        return urllib.request.urlopen(request, timeout=3)

    def test_mobile_page_is_local_and_serves_without_cdn(self):
        with self.request("/", token=False) as response:
            html = response.read().decode()
            self.assertIn('data-action="resume"', html)
            self.assertNotIn('src="http', html)
            self.assertIn("no-store", response.headers["Cache-Control"])

    def test_status_requires_pairing_token(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("/api/status", token=False)
        self.assertEqual(context.exception.code, 401)

    def test_status_has_size_and_no_obs_credentials(self):
        with self.request("/api/status") as response:
            state = json.load(response)
            self.assertTrue(state["connected"])
            self.assertIn("size_text", state)
            self.assertNotIn("obs_password", state)

    def test_phone_can_start_pause_resume_stop(self):
        for action in ("start", "pause", "resume", "stop"):
            with self.request("/api/control", {"action": action}) as response:
                result = json.load(response)
                self.assertTrue(result["ok"])
        self.assertEqual(self.fake.requests, ["StartRecord", "PauseRecord", "ResumeRecord", "StopRecord"])

    def test_cross_origin_control_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("/api/control", {"action": "start"}, origin="http://unrelated.example")
        self.assertEqual(context.exception.code, 403)
        self.assertEqual(self.fake.requests, [])

    def test_phone_sound_toggle_is_shared_and_persisted(self):
        with patch.object(self.config, "save") as save:
            with self.request("/api/sound", {"enabled": False}) as response:
                result = json.load(response)
            self.assertFalse(result["state"]["sound_enabled"])
            self.assertFalse(self.controller.config.sound_enabled)
            save.assert_called_once()

    def test_invalid_action_does_not_reach_obs(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("/api/control", {"action": "DeleteEverything"})
        self.assertEqual(context.exception.code, 400)
        self.assertEqual(self.fake.requests, [])

    def test_malformed_payload_is_rejected_without_control(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("/api/control", ["start"])
        self.assertEqual(context.exception.code, 400)
        self.assertEqual(self.fake.requests, [])

    def test_sound_setting_requires_boolean(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("/api/sound", {"enabled": "false"})
        self.assertEqual(context.exception.code, 400)
        self.assertTrue(self.config.sound_enabled)

    def test_server_shutdown_closes_listener(self):
        port = self.server.port
        self.server.stop()
        import socket
        with socket.socket() as connection:
            connection.settimeout(1)
            self.assertNotEqual(connection.connect_ex(("127.0.0.1", port)), 0)


if __name__ == "__main__":
    unittest.main()
