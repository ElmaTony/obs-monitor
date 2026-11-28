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
    def test_format_size_promotes_unit_when_rounding_reaches_1024(self):
        self.assertEqual(core.format_size(1023), "1023 B")
        self.assertEqual(core.format_size(1024), "1.00 KB")
        self.assertEqual(core.format_size(1024 * 1024 - 5), "1.00 MB")
        self.assertEqual(core.format_size(1024**3 - 5120), "1.00 GB")

    def test_close_frame_4009_reports_authentication_failure(self):
        import struct
        import websocket
        from websockets.sync.server import serve

        def handler(connection):
            connection.send(json.dumps({"op": 0, "d": {"rpcVersion": 1, "obsWebSocketVersion": "5.6.0", "authentication": {"salt": "s", "challenge": "c"}}}))
            connection.recv()
            connection.close(code=4009, reason="Authentication failed.")

        server = serve(handler, "127.0.0.1", 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = core.OBSWebSocketClient("127.0.0.1", server.socket.getsockname()[1], "wrong-password")
            with self.assertRaisesRegex(core.OBSRequestError, "密码"):
                client.connect()
        finally:
            server.shutdown()
            worker.join(timeout=2)

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

    def test_null_response_data_returns_empty_dict_and_rejects_invalid_video_settings(self):
        socket = FakeSocket()
        original_recv = socket.recv
        def recv_null():
            if socket.handshake >= 2:
                request = socket.sent[-1]["d"]
                return json.dumps({"op": 7, "d": {"requestId": request["requestId"], "requestStatus": {"result": True, "code": 100}, "responseData": None}})
            return original_recv()
        socket.recv = recv_null
        client = core.OBSWebSocketClient("127.0.0.1", 4455, "secret", socket_factory=lambda *a, **k: socket)
        client.connect()
        self.assertEqual(client.request("StopRecord"), {})
        with self.assertRaisesRegex(ValueError, "帧率"):
            client.get_frame_interval()

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

    def test_empty_and_malformed_websocket_frames_raise_connection_error(self):
        for payload in ("", b"", "not-json", "null", "[]"):
            with self.subTest(payload=payload):
                sock = MagicMock()
                sock.recv.return_value = payload
                client = core.OBSWebSocketClient("127.0.0.1", 4455, "", socket_factory=lambda *a, **k: sock)
                with self.assertRaises(ConnectionError):
                    client.connect()
                client.ws = sock
                with self.assertRaises(ConnectionError):
                    client.request("GetRecordStatus")

    def test_request_tightens_socket_timeout_across_event_frames(self):
        timeouts = []
        class EventThenReplySocket:
            def __init__(self):
                self.sent = []
                self.calls = 0
            def settimeout(self, value):
                timeouts.append(value)
            def send(self, value):
                self.sent.append(json.loads(value))
            def recv(self):
                self.calls += 1
                if self.calls == 1:
                    return json.dumps({"op": 5, "d": {"eventType": "RecordStateChanged"}})
                request_id = self.sent[-1]["d"]["requestId"]
                return json.dumps({"op": 7, "d": {"requestId": request_id, "requestStatus": {"result": True, "code": 100}, "responseData": {"ok": True}}})
        clock = iter([10.0, 10.0, 11.2, 11.2])
        client = core.OBSWebSocketClient("127.0.0.1", 4455, "", timeout=2.5)
        client.ws = EventThenReplySocket()
        with patch.object(core.time, "monotonic", side_effect=lambda: next(clock)):
            self.assertEqual(client.request("GetVersion"), {"ok": True})
        self.assertEqual(len(timeouts), 3)
        self.assertAlmostEqual(timeouts[0], 2.5)
        self.assertAlmostEqual(timeouts[1], 1.3)
        self.assertAlmostEqual(timeouts[2], 2.5)


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
    def test_boundary_flip_control_timeout_covers_full_settling_window_and_recovers_after_idle(self):
        clock = [0.0]
        observed_busy_timeouts = []
        self.fake.active = True
        self.fake.get_frame_interval = lambda: 6.0  # _settle=18s, _drain=384s
        self.controller._client = self.fake
        original_status = self.fake.get_record_status

        def status():
            if self.fake.requests and clock[0] < 384.0:
                return {"outputActive": True, "outputPaused": False, "outputBytes": 1000, "outputDuration": 1000}
            return original_status()

        def advance(seconds):
            observed_busy_timeouts.append(self.controller.control_timeout)
            clock[0] += seconds
            return False

        self.fake.get_record_status = status
        future = Future()
        with patch.object(core.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(self.controller._stop, "wait", side_effect=advance):
            self.controller._execute("stop", future, 0.0)
        self.assertTrue(future.result()["ok"])
        # During busy transition, timeout must exceed flip_deadline + 2 * _settle_seconds (384 + 36 = 420s).
        self.assertTrue(all(t >= 420.0 for t in observed_busy_timeouts))
        self.assertLess(self.controller.control_timeout, 40.0)

    def test_raising_threshold_or_disabling_alert_resets_alerted_state(self):
        self.controller._observe({"outputActive": True, "outputBytes": 200 * 1024**2, "outputDuration": 2000})
        self.assertTrue(self.controller.snapshot()["alerted"])
        raised = settings.AppConfig(threshold_gb=1.0, alert_enabled=True)
        with patch.object(raised, "save"):
            self.controller.apply_connection_config(raised)
        self.assertFalse(self.controller.snapshot()["alerted"])
        self.controller._observe({"outputActive": True, "outputBytes": 1100 * 1024**2, "outputDuration": 4000})
        self.assertTrue(self.controller.snapshot()["alerted"])
        self.assertEqual(len([k for k, _ in self.events if k == "size_alert"]), 2)
        disabled = settings.AppConfig(threshold_gb=1.0, alert_enabled=False)
        with patch.object(disabled, "save"):
            self.controller.apply_connection_config(disabled)
        self.assertFalse(self.controller.snapshot()["alerted"])

    def test_external_stop_during_pause_confirmation_emits_stop_and_fails_promptly(self):
        clock = [0.0]
        self.fake.active = True
        self.fake.paused = False
        self.fake.get_frame_interval = lambda: 2.0  # base_deadline would wait > 10s
        self.controller._client = self.fake
        self.fake.request = lambda name: self.fake.requests.append(name) or {}

        def external_stop():
            return {"outputActive": False, "outputPaused": False, "outputBytes": 5000, "outputDuration": 3000}

        self.fake.get_record_status = lambda: {"outputActive": True, "outputPaused": False, "outputBytes": 4000, "outputDuration": 2000}
        # Switch status to stopped right after PauseRecord request is issued.
        original_request = self.fake.request
        def request_then_stop(name):
            res = original_request(name)
            self.fake.get_record_status = external_stop
            return res
        self.fake.request = request_then_stop
        future = Future()
        with patch.object(core.time, "monotonic", side_effect=lambda: clock[0]):
            self.controller._execute("pause", future, 0.0)
        with self.assertRaisesRegex(core.OBSRequestError, "已结束"):
            future.result()
        self.assertEqual([p for k, p in self.events if k == "action"], ["stop"])
        self.assertEqual(clock[0], 0.0)

    def test_apply_connection_config_blocks_concurrent_commands_while_saving(self):
        entered, release = threading.Event(), threading.Event()
        self.connected()
        candidate = settings.AppConfig(obs_port=4567)

        def slow_save(save_password=True):
            entered.set()
            release.wait(2)

        with patch.object(candidate, "save", side_effect=slow_save):
            worker = threading.Thread(target=lambda: self.controller.apply_connection_config(candidate))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                pending = self.controller.submit("start")
                time.sleep(0.05)
                self.assertEqual(self.fake.requests, [])
            finally:
                release.set()
                worker.join(2)
        with self.assertRaises(ConnectionError):
            pending.result(timeout=1)

    def test_observe_never_invokes_callback_while_holding_controller_lock(self):
        lock_free = []

        def check_lock(kind, payload):
            acquired = []
            probe = threading.Thread(target=lambda: (
                acquired.append(self.controller._lock.acquire(blocking=False)),
                acquired[0] and self.controller._lock.release(),
            ))
            probe.start()
            probe.join(1)
            lock_free.append((kind, acquired[0]))

        self.controller.on_event = check_lock
        self.controller._client = self.fake
        future = Future()
        self.controller._execute("start", future, time.monotonic())
        future.result(timeout=2)
        self.assertTrue(lock_free)
        self.assertTrue(all(free for _, free in lock_free))

    def test_snapshot_caches_formatted_text_when_counters_unchanged(self):
        self.controller._observe({"outputActive": True, "outputBytes": 123456, "outputDuration": 5000})
        with patch.object(core, "format_size", wraps=core.format_size) as fmt_size, \
             patch.object(core, "format_duration", wraps=core.format_duration) as fmt_dur:
            first = self.controller.snapshot()
            second = self.controller.snapshot()
        self.assertEqual(fmt_size.call_count, 1)
        self.assertEqual(fmt_dur.call_count, 1)
        self.assertEqual(first["size_text"], second["size_text"])
        self.assertEqual(first["duration_text"], second["duration_text"])

    def test_closing_while_confirmation_arrives_cannot_restore_connected_state(self):
        self.controller._client = self.fake
        original = self.fake.get_record_status
        count = [0]
        def closing_confirmation():
            count[0] += 1
            data = original()
            if count[0] == 2:
                self.controller.stop()
            return data
        self.fake.get_record_status = closing_confirmation
        future = Future()
        self.controller._execute('start', future, time.monotonic())
        with self.assertRaises(core.OBSRequestError):
            future.result()
        self.assertFalse(self.controller.snapshot()['connected'])
        self.assertFalse(any(kind == 'action' for kind, _ in self.events))

    def test_obs_connection_cannot_change_during_an_unconfirmed_control(self):
        self.controller._update(busy=True)
        candidate = settings.AppConfig(obs_port=4567)
        with patch.object(candidate, 'save') as save:
            with self.assertRaisesRegex(ValueError, '操作'):
                self.controller.apply_connection_config(candidate)
        save.assert_not_called()
        self.assertIs(self.controller.config, self.config)

    def test_switching_obs_during_preflight_cannot_restore_or_control_old_obs(self):
        self.controller._client = self.fake
        candidate = settings.AppConfig(obs_host='192.0.2.2')
        def switching_status():
            with patch.object(candidate, 'save'):
                self.controller.apply_connection_config(candidate)
            return {'outputActive': True, 'outputBytes': 9999, 'outputDuration': 1000}
        self.fake.get_record_status = switching_status
        future = Future()
        self.controller._execute('pause', future, time.monotonic())
        with self.assertRaises(core.OBSRequestError):
            future.result()
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.controller.snapshot()['bytes'], 0)
        self.assertFalse(self.controller.snapshot()['connected'])

    def test_cancelled_pending_commands_can_be_drained_on_reconnect(self):
        self.controller._observe({'outputActive': True})
        first = self.controller.submit('pause')
        second = self.controller.submit('stop')
        self.assertTrue(first.cancel())
        self.controller.reconnect()
        self.assertTrue(first.cancelled())
        with self.assertRaises(ConnectionError):
            second.result(timeout=1)
        self.assertTrue(self.controller._commands.empty())

    def test_switching_obs_clears_old_data_and_rejects_queued_commands(self):
        self.controller._observe({'outputActive': True, 'outputBytes': 200 * 1024**2,
                                  'outputDuration': 10000})
        pending = self.controller.submit('pause')
        candidate = settings.AppConfig(obs_host='192.0.2.2')
        with patch.object(candidate, 'save'):
            self.controller.apply_connection_config(candidate)
        state = self.controller.snapshot()
        self.assertFalse(state['connected'])
        self.assertEqual(state['bytes'], 0)
        self.assertFalse(state['alerted'])
        self.assertTrue(pending.done())
        with self.assertRaises(ConnectionError):
            pending.result()
        self.assertFalse(any(state['allowed'].values()))

    def test_closing_during_preflight_does_not_send_a_recording_command(self):
        self.controller._client = self.fake
        def closing_status():
            self.controller.stop()
            return {'outputActive': False}
        self.fake.get_record_status = closing_status
        future = Future()
        self.controller._execute('start', future, time.monotonic())
        self.assertTrue(future.done())
        self.assertEqual(self.fake.requests, [])
        with self.assertRaises(core.OBSRequestError):
            future.result()

    def test_stopped_controller_rejects_commands_even_with_a_last_connected_sample(self):
        self.controller._observe({'outputActive': True, 'outputBytes': 1234})
        self.controller.stop()
        future = self.controller.submit('stop')
        self.assertTrue(future.done())
        with self.assertRaises(ConnectionError):
            future.result()
        self.assertTrue(self.controller._commands.empty())

    def test_frame_rate_changed_without_reconnect_is_used_by_next_command(self):
        self.connected()
        self.fake.get_frame_interval = lambda: 1 / 5
        started = time.monotonic()
        self.controller.submit('start').result(timeout=2)
        self.assertGreaterEqual(time.monotonic() - started, 0.6)

    def test_slow_frame_rate_can_finish_its_stability_window(self):
        clock = [0.0]
        self.controller._client = self.fake
        self.fake.get_frame_interval = lambda: 2.0
        self.controller._settle_seconds = 6.0
        def advance(seconds):
            clock[0] += seconds
            return False
        future = Future()
        with patch.object(core.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(self.controller._stop, 'wait', side_effect=advance):
            self.controller._execute('start', future, 0)
        self.assertTrue(future.result()['ok'])
        self.assertGreaterEqual(clock[0], 6)

    def test_fractional_fps_stop_waits_for_encoder_drain_without_replaying(self):
        clock = [0.0]
        draining = [False]
        self.fake.active, self.fake.paused = True, True
        self.fake.get_frame_interval = lambda: 6.0
        self.controller._client = self.fake
        original_status = self.fake.get_record_status
        def status():
            if draining[0]:
                self.fake.active = clock[0] < 106
                self.fake.bytes = int(min(clock[0], 106) * 100)
            return original_status()
        def request(name):
            self.fake.requests.append(name)
            draining[0], self.fake.paused = True, False
            return {"outputPath": "D:/recording.mkv"}
        def advance(seconds):
            clock[0] += seconds
            return False
        self.fake.get_record_status, self.fake.request = status, request
        future = Future()
        with patch.object(core.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(self.controller._stop, 'wait', side_effect=advance):
            self.controller._execute('stop', future, 0)
        self.assertTrue(future.result()['ok'])
        self.assertLess(future.result()['state']['control_timeout_ms'], 40000)
        self.assertFalse(future.result()['state']['busy'])
        self.assertGreaterEqual(clock[0], 124)
        self.assertLess(clock[0], 125)
        self.assertEqual(self.fake.requests, ['StopRecord'])
        self.assertEqual([p for k, p in self.events if k == 'action'], ['stop'])
        self.assertFalse(self.controller.snapshot()['busy'])

    def test_preflight_failure_before_sending_command_reports_not_executed(self):
        self.controller._client = self.fake
        self.fake.get_frame_interval = MagicMock(side_effect=ConnectionError("socket closed"))
        observed_busy = []
        future = Future()
        future.add_done_callback(lambda _: observed_busy.append(self.controller.snapshot()['busy']))
        with self.assertRaises(ConnectionError):
            self.controller._execute('start', future, time.monotonic())
        with self.assertRaisesRegex(ConnectionError, '操作未执行'):
            future.result()
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(observed_busy, [False])

    def test_failed_command_clears_busy_before_resolving_future(self):
        self.controller._client = self.fake
        self.fake.active = True
        observed_busy = []
        future = Future()
        future.add_done_callback(lambda _: observed_busy.append(self.controller.snapshot()['busy']))
        self.controller._execute('start', future, time.monotonic())
        with self.assertRaises(core.OBSRequestError):
            future.result()
        self.assertEqual(observed_busy, [False])

    def test_null_stop_response_data_still_confirms_stop_without_error(self):
        self.fake.active = True
        original = self.fake.request
        def stop_without_payload(name):
            original(name)
            return None
        self.fake.request = stop_without_payload
        self.connected()
        result = self.controller.submit("stop").result(timeout=2)
        self.assertTrue(result["ok"])
        self.assertFalse(result["state"]["active"])

    def test_unfinished_encoder_drain_has_a_finite_deadline_and_no_success_sound(self):
        clock = [0.0]
        self.fake.active = True
        self.fake.get_frame_interval = lambda: 6.0
        self.fake.request = lambda name: self.fake.requests.append(name) or {}
        self.controller._client = self.fake
        def advance(seconds):
            clock[0] += seconds
            self.fake.bytes += 1  # Ongoing output cannot extend the wait forever.
            return False
        future = Future()
        with patch.object(core.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(self.controller._stop, 'wait', side_effect=advance):
            self.controller._execute('stop', future, 0)
        with self.assertRaisesRegex(core.OBSRequestError, '尚未完成收尾'):
            future.result()
        self.assertLess(clock[0], 403)
        self.assertFalse(any(k == 'action' for k, _ in self.events))
        self.assertFalse(self.controller.snapshot()['busy'])
        self.assertLess(self.controller.control_timeout, 40)
        self.assertEqual(self.fake.requests, ['StopRecord'])

    def test_normal_fps_stop_confirms_promptly_and_clears_its_wait_budget(self):
        clock = [0.0]
        budgets = []
        self.fake.active = True
        self.fake.get_frame_interval = lambda: 1 / 60
        self.controller._client = self.fake
        def advance(seconds):
            budgets.append(self.controller.control_timeout)
            clock[0] += seconds
            return False
        future = Future()
        with patch.object(core.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(self.controller._stop, 'wait', side_effect=advance):
            self.controller._execute('stop', future, 0)
        self.assertTrue(future.result()['ok'])
        self.assertLess(clock[0], 0.4)
        self.assertTrue(all(budget < 15 for budget in budgets))
        self.assertLess(self.controller.control_timeout, 15)

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

    def test_fractional_fps_pause_waits_beyond_default_confirmation_timeout(self):
        clock = [0.0]
        self.fake.active = True
        self.fake.paused = False
        self.fake.get_frame_interval = lambda: 6.0
        original_status = self.fake.get_record_status

        def delayed_pause_status():
            if "PauseRecord" in self.fake.requests and clock[0] < 5.5:
                return {"outputActive": True, "outputPaused": False, "outputBytes": 1000, "outputDuration": 1000}
            return original_status()

        self.fake.get_record_status = delayed_pause_status
        self.controller._client = self.fake

        def advance(seconds):
            clock[0] += seconds
            return False

        future = Future()
        with patch.object(core.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(self.controller._stop, "wait", side_effect=advance):
            self.controller._execute("pause", future, 0.0)
        self.assertTrue(future.result()["ok"])
        self.assertGreaterEqual(clock[0], 23.5)

    def test_preflight_json_decode_error_reports_connection_error_not_request_error(self):
        self.controller._client = self.fake
        self.fake.get_record_status = MagicMock(side_effect=json.JSONDecodeError("Expecting value", "", 0))
        future = Future()
        with self.assertRaises(json.JSONDecodeError):
            self.controller._execute("start", future, time.monotonic())
        with self.assertRaisesRegex(ConnectionError, "连接中断"):
            future.result()

    def test_apply_connection_config_closes_stale_client_immediately(self):
        stale_client = MagicMock()
        self.controller._client = stale_client
        candidate = settings.AppConfig(obs_host="192.168.1.99", obs_port=4455)
        with patch.object(candidate, "save"):
            self.controller.apply_connection_config(candidate)
        stale_client.close.assert_called_once()

    def test_reconnect_and_config_change_clear_last_error_for_repeated_failure(self):
        bad = MagicMock()
        bad.connect.side_effect = ConnectionError("refused")
        self.controller._last_error = "无法连接 OBS：refused"
        self.controller.reconnect()
        self.assertEqual(self.controller._last_error, "")
        self.controller._last_error = "无法连接 OBS：refused"
        candidate = settings.AppConfig(obs_port=4456)
        with patch.object(candidate, "save"):
            self.controller.apply_connection_config(candidate)
        self.assertEqual(self.controller._last_error, "")

    def test_boundary_flip_completes_settling_window(self):
        clock = [0.0]
        self.controller.confirmation_timeout = 1.0
        self.controller._client = self.fake
        self.fake.get_frame_interval = lambda: 0.1  # settle = 0.3s, base_deadline = 1.3s
        original_status = self.fake.get_record_status
        def delayed_flip():
            self.fake.active = clock[0] >= 1.25
            return original_status()
        def advance(seconds):
            clock[0] += seconds
            return False
        self.fake.request = lambda name: self.fake.requests.append(name) or {}
        self.fake.get_record_status = delayed_flip
        future = Future()
        with patch.object(core.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(self.controller._stop, "wait", side_effect=advance):
            self.controller._execute("start", future, 0.0)
        self.assertTrue(future.result()["ok"])
        self.assertGreaterEqual(clock[0], 1.5)

    def test_extreme_low_fps_budget_is_capped(self):
        clock = [0.0]
        self.fake.active = True
        self.controller._client = self.fake
        self.fake.get_frame_interval = lambda: 60.0
        def advance(seconds):
            clock[0] += seconds
            return False
        future = Future()
        with patch.object(core.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(self.controller._stop, "wait", side_effect=advance):
            self.controller._execute("stop", future, 0.0)
        self.assertTrue(future.result()["ok"])
        self.assertLessEqual(self.controller._settle_seconds, 30.0)
        self.assertLessEqual(self.controller.control_timeout, 640.0)

    def test_slow_config_save_does_not_block_snapshot_or_expose_uncommitted_value(self):
        entered, release = threading.Event(), threading.Event()
        def slow_save():
            entered.set()
            release.wait(2)
        with patch.object(self.config, "save", side_effect=slow_save):
            worker = threading.Thread(target=lambda: self.controller.set_sound_enabled(False))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                snap = self.controller.snapshot()
                self.assertTrue(snap["sound_enabled"])
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(self.controller.snapshot()["sound_enabled"])


class SettingsTests(unittest.TestCase):
    def test_empty_appdata_falls_back_to_home_directory(self):
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location("_platform_support_sandbox", Path(__file__).parent / "platform_support.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"APPDATA": ""}, clear=False):
            spec.loader.exec_module(module)
        self.assertEqual(module.APP_DIR, Path.home() / "OBSAudioMonitor")
        self.assertTrue(module.APP_DIR.is_absolute())

    def test_load_avoids_self_connection_when_obs_and_remote_ports_match(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "CONFIG_PATH", Path(tmp) / "config.json"), \
             patch.object(settings, "detect_obs_websocket_settings", return_value=("127.0.0.1", 8765, "")), \
             patch.object(settings, "WindowsCredentialStore") as store:
            store.return_value.read.return_value = ""
            settings.CONFIG_PATH.write_text(json.dumps({"obs_host": "127.0.0.1", "obs_port": 8765, "remote_port": 8765, "remote_enabled": True}), encoding="utf-8")
            loaded = settings.AppConfig.load()
            self.assertNotEqual(loaded.obs_port, loaded.remote_port)
            self.assertEqual(loaded.remote_port, 8766)

    def test_validate_obs_host_rejects_percent_zone_id(self):
        with self.assertRaises(ValueError):
            settings.validate_obs_host("fe80::1%12")

    def test_lan_addresses_falls_back_to_private_probes_and_getaddrinfo_when_hosts_is_loopback(self):
        from remote_server import lan_addresses
        def fake_connect(addr):
            if addr[0] == "192.0.2.1":
                raise OSError("no internet")
        sock = MagicMock()
        sock.__enter__.return_value.connect.side_effect = fake_connect
        sock.__enter__.return_value.getsockname.return_value = ("192.168.50.10", 12345)
        with patch("remote_server.socket.socket", return_value=sock), \
             patch("remote_server.socket.gethostbyname_ex", return_value=("pc", [], ["127.0.0.1"])):
            self.assertEqual(lan_addresses(), ["192.168.50.10"])
        offline_sock = MagicMock()
        offline_sock.__enter__.return_value.connect.side_effect = OSError("unreachable")
        with patch("remote_server.socket.socket", return_value=offline_sock), \
             patch("remote_server.socket.gethostbyname_ex", return_value=("pc", [], ["127.0.0.1"])), \
             patch("remote_server.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.50.11", 0))]):
            self.assertEqual(lan_addresses(), ["192.168.50.11"])

    def test_title_bar_theme_caches_attributes_and_skips_unsupported_colors(self):
        import platform_support
        calls = []
        def fake_setter(hwnd, attr, ptr, size):
            calls.append((hwnd, attr))
            return 0x80070057 if attr in (35, 36) else 0
        with patch.object(platform_support, "_dwm_setter", return_value=fake_setter), \
             patch.object(platform_support, "_TITLE_BAR_CACHE", {}), \
             patch.object(platform_support, "_UNSUPPORTED_DWM_ATTRS", set()):
            platform_support.set_title_bar_theme(101, True, "#161514", "#f3efe8")
            platform_support.set_title_bar_theme(101, True, "#100f0e", "#ffffff")
        self.assertEqual(calls, [(101, 20), (101, 35), (101, 36)])

    def test_default_route_is_preferred_over_virtual_adapter_addresses(self):
        from remote_server import lan_addresses
        with patch('remote_server.socket.socket') as sock, \
             patch('remote_server.socket.gethostbyname_ex', return_value=('pc', [], ['192.168.56.1', '10.0.0.20'])):
            sock.return_value.__enter__.return_value.getsockname.return_value = ('10.0.0.20', 12345)
            self.assertEqual(lan_addresses()[0], '10.0.0.20')

    def test_first_run_help_flag_is_persisted_and_legacy_config_defaults_to_unseen(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, 'CONFIG_PATH', Path(tmp)/'config.json'), \
             patch.object(settings, 'detect_obs_websocket_settings', return_value=('127.0.0.1',4455,'')), \
             patch.object(settings, 'WindowsCredentialStore') as store:
            store.return_value.read.return_value = ''
            self.assertFalse(settings.AppConfig.load().help_seen)
            settings.CONFIG_PATH.write_text('{"theme":"dark"}')
            config = settings.AppConfig.load()
            self.assertFalse(config.help_seen)
            config.help_seen = True; config.save()
            self.assertTrue(settings.AppConfig.load().help_seen)

    def test_nonfinite_detected_obs_port_does_not_crash_startup(self):
        import platform_support
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'obs-studio' / 'plugin_config' / 'obs-websocket' / 'config.json'
            path.parent.mkdir(parents=True)
            path.write_text('{"server_port": 1e309}', encoding='utf-8')
            with patch.dict(platform_support.os.environ, {'APPDATA': temporary}):
                self.assertEqual(platform_support.detect_obs_websocket_settings()[1], 4455)

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

    def test_lan_addresses_filters_unspecified_multicast_and_invalid_entries(self):
        from remote_server import lan_addresses
        with patch("remote_server.socket.socket") as sock, \
             patch("remote_server.socket.gethostbyname_ex", return_value=("pc", [], ["0.0.0.0", "224.0.0.1", "not-an-ip", "192.168.1.10"])):
            sock.return_value.__enter__.return_value.getsockname.return_value = ("0.0.0.0", 12345)
            self.assertEqual(lan_addresses(), ["192.168.1.10"])

    def test_boolean_values_in_config_are_not_coerced_to_ports_or_threshold(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "CONFIG_PATH", Path(tmp) / "config.json"), \
             patch.object(settings, "detect_obs_websocket_settings", return_value=("127.0.0.1", 4455, "")), \
             patch.object(settings, "WindowsCredentialStore") as store:
            store.return_value.read.return_value = ""
            settings.CONFIG_PATH.write_text(json.dumps({"obs_port": True, "remote_port": False, "threshold_gb": True}))
            loaded = settings.AppConfig.load()
            self.assertEqual(loaded.obs_port, 4455)
            self.assertEqual(loaded.remote_port, 8765)
            self.assertEqual(loaded.threshold_gb, 2.0)

    def test_detect_obs_websocket_rejects_boolean_out_of_range_and_empty_appdata(self):
        import platform_support
        with patch.dict(platform_support.os.environ, {"APPDATA": ""}):
            self.assertEqual(platform_support.detect_obs_websocket_settings(), ("127.0.0.1", 4455, ""))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json"
            path.parent.mkdir(parents=True)
            for bad_port in (True, False, 0, -5, 99999):
                with self.subTest(bad_port=bad_port):
                    path.write_text(json.dumps({"server_enabled": True, "server_port": bad_port}), encoding="utf-8")
                    with patch.dict(platform_support.os.environ, {"APPDATA": temporary}):
                        self.assertEqual(platform_support.detect_obs_websocket_settings()[1], 4455)

    def test_remote_obs_host_with_empty_credential_does_not_inherit_local_password(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "CONFIG_PATH", Path(tmp) / "config.json"), \
             patch.object(settings, "detect_obs_websocket_settings", return_value=("127.0.0.1", 4455, "local-secret")), \
             patch.object(settings, "WindowsCredentialStore") as store:
            store.return_value.read.return_value = ""
            settings.CONFIG_PATH.write_text(json.dumps({"obs_host": "192.168.1.50"}), encoding="utf-8")
            self.assertEqual(settings.AppConfig.load().obs_password, "")
            settings.CONFIG_PATH.write_text(json.dumps({"obs_host": "127.0.0.1"}), encoding="utf-8")
            self.assertEqual(settings.AppConfig.load().obs_password, "local-secret")

    def test_detect_obs_websocket_clears_password_when_auth_disabled(self):
        import platform_support
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "server_enabled": True, "server_port": 4456,
                "auth_required": False, "server_password": "stale-password",
            }), encoding="utf-8")
            with patch.dict(platform_support.os.environ, {"APPDATA": temporary}):
                self.assertEqual(platform_support.detect_obs_websocket_settings(), ("127.0.0.1", 4456, ""))

    def test_corrupted_credential_allows_password_overwrite_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "CONFIG_PATH", Path(tmp) / "config.json"), \
             patch.object(settings, "WindowsCredentialStore") as store:
            store.return_value.read.side_effect = ValueError("corrupted")
            settings.AppConfig(obs_password="new-password").save(save_password=True)
            store.return_value.write.assert_called_once_with("new-password")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["config.json"])
            store.return_value.write.reset_mock()
            with patch.object(Path, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    settings.AppConfig(obs_password="another-password").save(save_password=True)
            store.return_value.delete.assert_called_once()

    def test_credential_read_oserror_fails_before_creating_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "CONFIG_PATH", Path(tmp) / "config.json"), \
             patch.object(settings, "WindowsCredentialStore") as store:
            store.return_value.read.side_effect = OSError("access denied")
            with self.assertRaises(OSError):
                settings.AppConfig(obs_password="secret").save(save_password=True)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_credential_store_validates_length_and_invalid_utf16(self):
        import ctypes
        import platform_support
        store = platform_support.WindowsCredentialStore()
        with self.assertRaises(ValueError):
            store.write("a" * 1500)
        bad_bytes = b"\xff\xd8"
        buf = (ctypes.c_byte * len(bad_bytes)).from_buffer_copy(bad_bytes)
        cred = platform_support._CREDENTIALW()
        cred.CredentialBlobSize = len(bad_bytes)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
        def fake_read(target, cred_type, flags, ptr_out):
            ptr_out._obj.contents = cred
            return 1
        fake_advapi = MagicMock()
        fake_advapi.CredReadW.side_effect = fake_read
        store._advapi32 = fake_advapi
        with self.assertRaises(ValueError):
            store.read()
        cred.CredentialBlobSize = 0
        cred.CredentialBlob = None
        self.assertEqual(store.read(), "")

    def test_disabled_new_obs_websocket_config_does_not_fall_back_to_legacy(self):
        import platform_support
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "obs-studio" / "plugin_config"
            (root / "obs-websocket").mkdir(parents=True)
            (root / "obs-websocket" / "config.json").write_text(json.dumps({"server_enabled": False, "server_port": 4457, "server_password": "new"}), encoding="utf-8")
            (root / "obs-websocket.json").write_text(json.dumps({"enabled": True, "port": 4458, "password": "legacy"}), encoding="utf-8")
            with patch.dict(platform_support.os.environ, {"APPDATA": temporary}):
                self.assertEqual(platform_support.detect_obs_websocket_settings(), ("127.0.0.1", 4455, ""))

    def test_load_ignores_invalid_obs_host_in_config_json(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(settings, "CONFIG_PATH", Path(tmp) / "config.json"), \
             patch.object(settings, "detect_obs_websocket_settings", return_value=("127.0.0.1", 4455, "")), \
             patch.object(settings, "WindowsCredentialStore") as store:
            store.return_value.read.return_value = ""
            settings.CONFIG_PATH.write_text(json.dumps({"obs_host": "127.0.0.1:9999"}), encoding="utf-8")
            self.assertEqual(settings.AppConfig.load().obs_host, "127.0.0.1")

    def test_lan_addresses_prefers_172_private_over_cgnat_and_falls_back_to_getaddrinfo(self):
        from remote_server import lan_addresses
        with patch("remote_server.socket.socket") as sock, \
             patch("remote_server.socket.gethostbyname_ex", return_value=("pc", [], ["100.64.0.5", "172.16.10.2"])):
            sock.return_value.__enter__.return_value.getsockname.return_value = ("100.64.0.5", 12345)
            self.assertEqual(lan_addresses(), ["172.16.10.2", "100.64.0.5"])
        with patch("remote_server.socket.socket") as sock, \
             patch("remote_server.socket.gethostbyname_ex", side_effect=OSError("legacy failed")), \
             patch("remote_server.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.33", 0))]):
            sock.return_value.__enter__.side_effect = OSError()
            self.assertEqual(lan_addresses(), ["192.168.1.33"])


class DesktopTests(unittest.TestCase):
    def test_occupied_remote_port_does_not_block_saving_obs_settings(self):
        self.app.remote = None
        candidate = settings.AppConfig(obs_port=4456, remote_enabled=True, remote_port=8765)
        with patch.object(self.app, "_create_remote", side_effect=OSError("port in use")), \
             patch.object(settings.AppConfig, "save"):
            self.app.apply_settings(candidate)
        self.assertEqual(self.app.config.obs_port, 4456)
        self.assertIsNone(self.app.remote)

    def test_disabling_remote_logs_stopped_message(self):
        self.start_phone()
        self.app.clear_activity_log()
        candidate = settings.AppConfig(remote_enabled=False, remote_port=self.config.remote_port)
        with patch.object(settings.AppConfig, "save"):
            self.app.apply_settings(candidate)
        self.assertTrue(any("手机遥控已关闭" in entry[2] for entry in self.app.activity_entries))

    def test_activity_item_sets_display_text_and_qr_cache_reuses_pixmap(self):
        item = self.app._make_activity_item("12:00:00", "info", "测试记录")
        self.assertEqual(item.text(), "12:00:00  测试记录")
        self._fake_phone_service()
        self.app._known_addresses = ["192.0.2.10"]
        self.app._sync_remote_ui(discover=False)
        with patch("desktop_ui.qrcode.QRCode") as qr_cls:
            self.app._refresh_qr()
        qr_cls.assert_not_called()

    def _fake_phone_service(self):
        self.app.remote = MagicMock()
        self.app.remote.url.side_effect = lambda address: f'http://{address}:8765/#token=test'

    def test_network_discovery_runs_off_the_gui_thread(self):
        self._fake_phone_service()
        observed = []
        ui_thread = threading.get_ident()
        def discover():
            observed.append(threading.get_ident())
            return ['192.0.2.10']
        with patch('desktop_ui.lan_addresses', side_effect=discover):
            self.app._sync_remote_ui()
            deadline = time.monotonic()+2
            while self.app._address_probe_running and time.monotonic()<deadline:
                self.qt.processEvents(); time.sleep(0.005)
        self.assertEqual(len(observed), 1)
        self.assertNotEqual(observed[0], ui_thread)
        self.assertEqual(self.app.address_combo.currentData(), '192.0.2.10')
        self.assertTrue(self.app.copy_button.isEnabled())

    def test_network_change_refreshes_link_and_preserves_a_valid_manual_selection(self):
        self._fake_phone_service()
        self.app._on_addresses_ready(['192.0.2.10','192.0.2.20'])
        self.app.address_combo.setCurrentIndex(1)
        self.app.address_combo.activated.emit(1)
        self.app._on_addresses_ready(['192.0.2.30','192.0.2.20'])
        self.assertEqual(self.app.address_combo.currentData(), '192.0.2.20')
        self.app._on_addresses_ready(['192.0.2.30'])
        self.app.copy_link()
        self.assertIn('192.0.2.30', self.qt.clipboard().text())

    def test_changed_default_route_updates_the_automatic_qr_address(self):
        self._fake_phone_service()
        self.app._on_addresses_ready(['192.0.2.10','192.0.2.20'])
        self.assertEqual(self.app.address_combo.currentData(), '192.0.2.10')
        self.app._on_addresses_ready(['192.0.2.20','192.0.2.10'])
        self.assertEqual(self.app.address_combo.currentData(), '192.0.2.20')

    def test_no_network_does_not_offer_a_loopback_phone_link(self):
        self._fake_phone_service()
        self.app._sync_remote_ui(discover=False)
        self.assertFalse(self.app.copy_button.isEnabled())
        self.assertTrue(self.app.open_button.isEnabled())
        self.app._on_addresses_ready(['192.0.2.10'])
        self.assertTrue(self.app.copy_button.isEnabled())
        self.app._on_addresses_ready([])
        self.assertFalse(self.app.copy_button.isEnabled())
        self.assertIsNone(self.app.address_combo.currentData())

    def test_late_network_discovery_cannot_update_a_closed_window(self):
        self.app.close()
        with patch.object(self.app, '_sync_remote_ui') as update:
            self.app._on_addresses_ready(['192.0.2.10'])
        update.assert_not_called()
        self.assertFalse(self.app.network_timer.isActive())

    def test_reselecting_current_theme_keeps_transition_and_avoids_another_save(self):
        self.app.show(); self.qt.processEvents()
        with patch('desktop_ui.animations_enabled', return_value=True), patch.object(self.config, 'save') as save:
            self.app.apply_theme('dark')
            animation = self.app._theme_animation
            self.app.theme_buttons['dark'].click()
            self.assertIs(self.app._theme_animation, animation)
            self.assertTrue(self.app.theme_buttons['dark'].isChecked())
            self.assertEqual(save.call_count, 1)

    def test_main_window_close_also_closes_owned_dialogs(self):
        from PySide6.QtWidgets import QDialog
        self.app.show(); self.app.open_settings(); self.app.show_help()
        self.qt.processEvents()
        self.app.close(); self.qt.processEvents()
        self.assertTrue(self.app.closed)
        self.assertFalse(any(dialog.isVisible() for dialog in self.app.findChildren(QDialog)))

    def test_closing_settings_during_theme_transition_releases_layers(self):
        self.app.show(); self.app.open_settings(); self.qt.processEvents()
        with patch('desktop_ui.animations_enabled', return_value=True), patch.object(self.config, 'save'):
            self.app.apply_theme('dark')
        self.app.settings_window.reject(); self.qt.processEvents()
        self.assertIsNone(self.app.settings_window)
        self.assertIsNone(self.app._theme_animation)
        self.assertEqual(self.app._theme_overlays, [])

    def test_closed_settings_dialogs_are_released(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QDialog
        self.app.show(); self.qt.processEvents()
        for _ in range(8):
            self.app.open_settings()
            self.app.settings_window.reject()
            self.qt.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertIsNone(self.app.settings_window)
        self.assertEqual(self.app.findChildren(QDialog), [])

    def test_first_run_help_opens_once_keeps_refreshing_and_can_be_opened_manually(self):
        self.app.show(); self.qt.processEvents()
        with patch.object(self.config, 'save') as save:
            self.app.show_first_run_help()
            dialog = self.app.help_window
            self.assertTrue(dialog.isVisible())
            self.assertTrue(self.config.help_seen)
            self.app.show_first_run_help()
            self.app.show_help()
            self.assertIs(self.app.help_window, dialog)
            self.assertEqual(save.call_count, 1)
        self.app.controller._observe({'outputActive':True,'outputBytes':123456})
        self.app._tick()
        self.assertEqual(self.app.size_exact_label.text(), '123,456 字节')
        dialog.accept()
        self.app.show_first_run_help()
        self.assertFalse(dialog.isVisible())
        self.app.show_help()
        self.assertIs(self.app.help_window, dialog)
        self.assertTrue(self.app.help_window.isVisible())
        self.app.help_window.accept()

    def test_reopened_app_does_not_show_first_run_help(self):
        self.config.help_seen = True
        self.app.show(); self.qt.processEvents()
        self.app.show_first_run_help()
        self.assertIsNone(self.app.help_window)

    def test_first_run_help_save_failure_is_retryable_and_does_not_close_help(self):
        self.app.show(); self.qt.processEvents()
        with patch.object(self.config, 'save', side_effect=OSError('disk full')):
            self.app.show_first_run_help()
        self.assertFalse(self.config.help_seen)
        self.assertTrue(self.app.help_window.isVisible())
        with patch.object(self.config, 'save'):
            self.app.show_first_run_help()
        self.assertTrue(self.config.help_seen)
        self.app.help_window.accept()

    def test_saving_connection_settings_preserves_first_run_help_state(self):
        self.config.help_seen = True
        candidate = settings.AppConfig()
        candidate.remote_enabled = False
        with patch.object(settings.AppConfig, 'save'):
            self.app.apply_settings(candidate)
        self.assertTrue(self.app.config.help_seen)

    def test_first_run_callback_after_close_does_not_open_dialog(self):
        self.app.close()
        with patch.object(self.config, 'save') as save:
            self.app.show_first_run_help()
        self.assertIsNone(self.app.help_window)
        save.assert_not_called()

    def test_number_fields_click_type_and_respect_limits(self):
        from desktop_ui import NumberField
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.app.open_settings(); self.qt.processEvents()
        fields = self.app.settings_window.findChildren(NumberField)
        self.assertEqual(len(fields), 3)
        for field in fields:
            spin = field.spin
            self.assertGreater(spin.lineEdit().width(), spin.width() // 2)
            original = spin.value()
            QTest.mouseClick(field.plus, Qt.MouseButton.LeftButton)
            self.assertEqual(spin.value(), original + spin.singleStep())
            QTest.mouseClick(field.minus, Qt.MouseButton.LeftButton)
            self.assertEqual(spin.value(), original)
            spin.setValue(spin.maximum())
            self.assertFalse(field.plus.isEnabled())
            spin.setValue(spin.minimum())
            self.assertFalse(field.minus.isEnabled())
        port = fields[0].spin
        port.setFocus(); port.selectAll()
        QTest.keyClicks(port, '6000')
        QTest.keyClick(port, Qt.Key.Key_Up)
        self.assertEqual(port.value(), 6001)

    def test_theme_transition_blends_and_repeated_switches_keep_live_state(self):
        from desktop_ui import PALETTES
        from PySide6.QtCore import Qt
        self.app.show(); self.qt.processEvents()
        self.app.controller._observe({'outputActive':True, 'outputBytes':12345})
        with patch('desktop_ui.animations_enabled', return_value=True), patch.object(self.config, 'save'):
            self.app.apply_theme('dark')
            self.assertIsNotNone(self.app._theme_animation)
            self.app._theme_animation.setCurrentTime(120)
            pixel = self.app.centralWidget().grab().toImage().pixelColor(0, 0)
            self.assertNotIn(pixel.name(), (PALETTES['light']['bg'], PALETTES['dark']['bg']))
            self.assertTrue(all(layer.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                                for layer in self.app._theme_overlays))
            self.app.controller._observe({'outputActive':True, 'outputPaused':True, 'outputBytes':54321})
            self.app._tick()
            self.app.apply_theme('light')
            self.app._theme_animation.setCurrentTime(240)
        self.assertIsNone(self.app._theme_animation)
        self.assertEqual(self.app._theme_overlays, [])
        self.assertEqual(self.app._title_colors['bg'], PALETTES['light']['bg'])
        self.assertTrue(self.app.theme_buttons['light'].isChecked())
        self.assertEqual(self.app.size_exact_label.text(), '54,321 字节')
        self.assertTrue(self.app.action_buttons['resume'].isEnabled())

    def test_failed_theme_save_during_transition_preserves_current_transition(self):
        self.app.show(); self.qt.processEvents()
        with patch('desktop_ui.animations_enabled', return_value=True), patch.object(self.config, 'save'):
            self.app.apply_theme('dark')
        animation = self.app._theme_animation
        with patch.object(self.config, 'save', side_effect=OSError('disk full')):
            self.app.apply_theme('light')
        self.assertIs(self.app._theme_animation, animation)
        self.assertEqual(self.config.theme, 'dark')
        self.assertTrue(self.app.theme_buttons['dark'].isChecked())
        animation.setCurrentTime(animation.duration())
        self.assertIsNone(self.app._theme_animation)

    def test_reduced_motion_and_resize_leave_no_transition_layer(self):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QResizeEvent
        self.app.show(); self.qt.processEvents()
        with patch('desktop_ui.animations_enabled', return_value=False), patch.object(self.config, 'save'):
            self.app.apply_theme('dark')
        self.assertIsNone(self.app._theme_animation)
        with patch('desktop_ui.animations_enabled', return_value=True), patch.object(self.config, 'save'):
            self.app.apply_theme('light')
        self.assertIsNotNone(self.app._theme_animation)
        self.qt.sendEvent(self.app, QResizeEvent(QSize(800, 540), self.app.size()))
        self.qt.processEvents()
        self.assertIsNone(self.app._theme_animation)
        self.assertEqual(self.app._theme_overlays, [])

    def test_main_window_uses_fixed_compact_size_and_clamps_for_small_screens(self):
        from PySide6.QtCore import QRect, Qt
        from desktop_ui import WINDOW_HEIGHT, WINDOW_WIDTH
        self.assertEqual(self.app.minimumSize(), self.app.maximumSize())
        self.assertFalse(bool(self.app.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint))
        self.app._apply_fixed_window_size(QRect(0, 0, 1920, 1040))
        self.assertEqual((self.app.width(), self.app.height()), (WINDOW_WIDTH, WINDOW_HEIGHT))
        self.app._apply_fixed_window_size(QRect(0, 0, 800, 540))
        self.assertEqual((self.app.width(), self.app.height()), (768, 508))
        self.assertEqual(self.app.minimumSize(), self.app.maximumSize())

    def test_activity_feed_records_newest_first_syncs_dialog_and_clears_cleanly(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QDialog
        from desktop_ui import ActivityDelegate
        self.app.show(); self.qt.processEvents()
        self.app._log('第一条操作', level='accent')
        self.app._log('第二条错误', level='red')
        self.assertEqual(self.app.activity_list.item(0).data(ActivityDelegate.ROLE_MESSAGE), '第二条错误')
        self.assertEqual(self.app.activity_list.item(0).data(ActivityDelegate.ROLE_LEVEL), 'red')
        self.app.open_activity_log(); self.qt.processEvents()
        self.assertIsNotNone(self.app.activity_window)
        self.assertTrue(self.app.activity_window.isVisible())
        self.app._log('实时同步记录', level='amber')
        self.assertEqual(self.app._activity_dialog_list.item(0).data(ActivityDelegate.ROLE_MESSAGE), '实时同步记录')
        self.app.clear_activity_log()
        self.assertEqual(self.app.activity_list.count(), 0)
        self.assertEqual(self.app._activity_dialog_list.count(), 0)
        self.assertTrue(self.app.activity_empty_label.isVisible())
        self.assertTrue(self.app._activity_dialog_empty.isVisible())
        self.assertFalse(self.app._activity_dialog_copy.isEnabled())
        self.assertFalse(self.app._activity_dialog_clear.isEnabled())
        self.app.activity_window.reject(); self.qt.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertIsNone(self.app.activity_window)
        self.assertEqual(self.app.findChildren(QDialog), [])

    def test_disabled_size_alert_keeps_progress_at_zero_and_muted_sound_dims_actions(self):
        self.app.show(); self.qt.processEvents()
        self.config.alert_enabled = False
        self.config.sound_enabled = False
        self.app.controller._observe({"outputActive": True, "outputBytes": 10 * 1024**3})
        self.app._tick()
        self.assertEqual(self.app.progress.value(), 0)
        self.assertTrue(self.app.sound_muted_label.isVisible())
        self.assertTrue(all(item._dimmed for item in self.app.sound_vars.values()))

    def test_switch_entire_visible_surface_accepts_clicks(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        self.app.show(); self.qt.processEvents()
        with patch.object(self.config, 'save'):
            QTest.mouseClick(self.app.sound_switch, Qt.MouseButton.LeftButton, pos=QPoint(34, 12))
        self.assertFalse(self.app.sound_switch.isChecked())
        self.assertFalse(self.config.sound_enabled)

    def test_failed_theme_save_restores_the_selected_theme_button(self):
        for theme in ('dark', 'light'):
            with self.subTest(theme=theme), patch.object(self.config, 'save', side_effect=OSError('disk full')):
                self.app.theme_buttons[theme].click()
            self.assertTrue(self.app.theme_buttons['light'].isChecked())
            self.assertFalse(self.app.theme_buttons['dark'].isChecked())
            self.assertEqual(self.config.theme, 'light')

    def remote_settings(self, port):
        from PySide6.QtWidgets import QPushButton, QSpinBox
        self.app.open_settings()
        dialog = self.app.settings_window
        dialog.findChildren(QSpinBox)[1].setValue(port)
        return next(button for button in dialog.findChildren(QPushButton)
                    if button.text() == '保存并应用')

    def start_phone(self):
        from pairing import PairingIdentity
        self.app.pairing_identity = PairingIdentity()
        self.app.remote = RemoteServer(self.app.controller, host='127.0.0.1', port=0,
                                       identity=self.app.pairing_identity)
        self.app.remote.start()
        self.config.remote_port = self.app.remote.port
        return self.app.remote

    def test_occupied_new_phone_port_preserves_working_server_and_settings(self):
        import socket
        old = self.start_phone()
        with socket.socket() as occupied:
            occupied.bind(('0.0.0.0', 0)); occupied.listen()
            save_button = self.remote_settings(occupied.getsockname()[1])
            with patch.object(settings.AppConfig, 'save'), patch('desktop_ui.QMessageBox.warning'):
                save_button.click()
        self.assertIs(self.app.remote, old)
        self.assertEqual(self.app.config.remote_port, old.port)
        with urllib.request.urlopen(f'http://127.0.0.1:{old.port}/', timeout=2) as response:
            self.assertEqual(response.status, 200)

    def test_failed_save_after_preparing_new_port_releases_it_and_keeps_old_server(self):
        import socket
        old = self.start_phone()
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0)); new_port = reserve.getsockname()[1]
        save_button = self.remote_settings(new_port)
        with patch.object(settings.AppConfig, 'save', side_effect=OSError('disk full')), \
             patch('desktop_ui.QMessageBox.warning'):
            save_button.click()
        self.assertIs(self.app.remote, old)
        with socket.socket() as available:
            available.bind(('0.0.0.0', new_port))

    def test_phone_port_change_preserves_pairing_and_closes_old_listener(self):
        import socket
        old = self.start_phone()
        token = old.token
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0)); new_port = reserve.getsockname()[1]
        save_button = self.remote_settings(new_port)
        with patch.object(settings.AppConfig, 'save'):
            save_button.click()
        self.assertIsNot(self.app.remote, old)
        self.assertEqual(self.app.remote.token, token)
        with socket.socket() as connection:
            self.assertNotEqual(connection.connect_ex(('127.0.0.1', old.port)), 0)
        request = urllib.request.Request(f'http://127.0.0.1:{new_port}/api/status',
                                         headers={'X-OBS-Token': token})
        with urllib.request.urlopen(request, timeout=2) as response:
            self.assertIn('revision', json.load(response))

    def test_sound_save_failure_restores_visible_switch(self):
        with patch.object(self.config, "save", side_effect=OSError("save failed")):
            self.app.sound_switch.setChecked(False)
        self.assertTrue(self.config.sound_enabled)
        self.assertTrue(self.app.sound_switch.isChecked())
        self.app._tick()
        self.assertTrue(self.app.sound_switch.isChecked())

    def test_initial_no_sample_does_not_label_stale_as_previous_data(self):
        self.app._tick()
        self.assertEqual(self.app.size_exact_label.text(), "尚未收到 OBS 数据")
        self.app.controller._observe({"outputActive": True, "outputBytes": 12345})
        self.app.controller._sampled_at = time.monotonic() - 4.0
        self.app._tick()
        self.assertEqual(self.app.size_exact_label.text(), "12,345 字节 · 上次数据")

    def test_settings_rejects_host_with_port_or_carriage_return(self):
        from PySide6.QtWidgets import QLineEdit, QPushButton
        for invalid in ("127.0.0.1:4455", "127.0\r.0.1"):
            with self.subTest(invalid=invalid):
                self.app.open_settings()
                dialog = self.app.settings_window
                dialog.findChildren(QLineEdit)[0].setText(invalid)
                save_button = next(b for b in dialog.findChildren(QPushButton) if b.text() == "保存并应用")
                with patch("desktop_ui.QMessageBox.warning") as warn, patch.object(settings.AppConfig, "save") as save:
                    save_button.click()
                warn.assert_called_once()
                save.assert_not_called()
                dialog.reject()
        self.app.open_settings()
        dialog = self.app.settings_window
        dialog.findChildren(QLineEdit)[0].setText("::1")
        save_button = next(b for b in dialog.findChildren(QPushButton) if b.text() == "保存并应用")
        with patch("desktop_ui.QMessageBox.warning") as warn, \
             patch.object(settings.AppConfig, "save") as save, \
             patch.object(self.app, "_create_remote", return_value=MagicMock(port=8765)):
            save_button.click()
        warn.assert_not_called()
        save.assert_called_once()
        self.assertEqual(self.app.config.obs_host, "::1")

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

    def test_desktop_request_error_logs_once_without_duplicate_entry(self):
        self.app.clear_activity_log()
        future = Future()
        future.set_exception(core.OBSRequestError("当前编码器不支持暂停"))
        with patch.object(self.app.controller, "submit", return_value=future):
            self.app._on_event("error", "当前编码器不支持暂停")
            self.app.perform("pause")
            self.app._tick()
        self.assertEqual(len(self.app.activity_entries), 1)
        self.assertEqual(self.app.activity_entries[0][2], "当前编码器不支持暂停")

    def test_settings_rejects_bracketed_ipv6_with_port_and_accepts_plain_ipv6(self):
        import desktop_ui
        for bad in ("[::1]:4455", "[::1", "::1]", "127.0.0.1:4455", "bad\x00host"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    desktop_ui._validate_obs_host(bad)
        self.assertEqual(desktop_ui._validate_obs_host("::1"), "::1")
        self.assertEqual(desktop_ui._validate_obs_host("[::1]"), "[::1]")

    def test_disabled_remote_disables_address_selector_and_shows_closed_state(self):
        self.app.remote = None
        self.app._known_addresses = ["192.0.2.10"]
        self.app._sync_remote_ui(discover=False)
        self.assertFalse(self.app.address_combo.isEnabled())
        self.assertEqual(self.app.address_combo.currentText(), "手机遥控已关闭")
        self.assertIsNone(self.app.address_combo.currentData())

    def test_repeated_copy_link_resets_feedback_timer(self):
        self._fake_phone_service()
        self.app._known_addresses = ["192.0.2.10"]
        self.app._sync_remote_ui(discover=False)
        self.app.copy_link()
        self.assertEqual(self.app.copy_button.text(), "已复制")
        self.assertTrue(self.app._copy_link_timer.isActive())
        self.app.copy_link()
        self.assertEqual(self.app.copy_button.text(), "已复制")
        self.app._copy_link_timer.timeout.emit()
        self.assertEqual(self.app.copy_button.text(), "复制链接")

    def test_large_threshold_gb_preserves_decimal_digit(self):
        self.config.threshold_gb = 100000.5
        self.app._tick()
        self.assertEqual(self.app.threshold_label.text(), "提醒于 100000.5 GB · 每次一次")

    def test_reset_pairing_works_when_remote_is_disabled(self):
        from PySide6.QtWidgets import QMessageBox, QPushButton
        self.app.remote = None
        self.app.open_settings()
        reset_btn = next(b for b in self.app.settings_window.findChildren(QPushButton) if b.text() == "重置所有手机配对")
        self.assertTrue(reset_btn.isEnabled())
        fake_identity = MagicMock()
        with patch("desktop_ui.PairingIdentity.persistent", return_value=fake_identity), \
             patch("desktop_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            reset_btn.click()
        fake_identity.rotate.assert_called_once()

    def test_non_dialog_popup_show_does_not_sync_title_bars(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QWidget
        popup = QWidget(self.app)
        with patch.object(self.app, "_sync_title_bars") as sync:
            self.app.eventFilter(popup, QEvent(QEvent.Type.Show))
        sync.assert_not_called()

    def test_activity_dialog_resize_finishes_theme_transition(self):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QResizeEvent
        self.app.show()
        self.app.open_activity_log()
        self.qt.processEvents()
        with patch("desktop_ui.animations_enabled", return_value=True), patch.object(self.config, "save"):
            self.app.apply_theme("dark")
        self.assertIsNotNone(self.app._theme_animation)
        self.qt.sendEvent(self.app.activity_window, QResizeEvent(QSize(560, 440), self.app.activity_window.size()))
        self.qt.processEvents()
        self.assertIsNone(self.app._theme_animation)
        self.assertEqual(self.app._theme_overlays, [])


class AudioTests(unittest.TestCase):
    def test_damaged_cached_tone_is_regenerated(self):
        import wave
        from platform_support import AudioPlayer
        with tempfile.TemporaryDirectory() as temporary, patch('platform_support.SOUND_DIR', Path(temporary)):
            path = Path(temporary)/'start.wav'
            path.write_bytes(b'incomplete wave')
            AudioPlayer()
            with wave.open(str(path), 'rb') as tone:
                self.assertGreater(tone.getnframes(), 0)

    def test_failed_tone_generation_preserves_the_previous_file(self):
        import wave
        from platform_support import AudioPlayer
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'start.wav'
            AudioPlayer._write_tone(path, AudioPlayer.SOUND_RECIPES['start'])
            before = path.read_bytes()
            with patch.object(wave.Wave_write, 'writeframes', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    AudioPlayer._write_tone(path, AudioPlayer.SOUND_RECIPES['start'])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual([p.name for p in Path(temporary).iterdir()], ['start.wav'])

    def test_play_regenerates_deleted_tone_file(self):
        from platform_support import AudioPlayer
        with tempfile.TemporaryDirectory() as temporary, patch('platform_support.SOUND_DIR', Path(temporary)), \
             patch('platform_support.winsound.PlaySound') as play_sound:
            player = AudioPlayer()
            tone_path = Path(temporary) / 'start.wav'
            tone_path.unlink()
            self.assertFalse(tone_path.exists())
            player.play('start')
            self.assertTrue(tone_path.exists())
            play_sound.assert_called_once()

    def test_play_regenerates_corrupted_tone_file(self):
        import wave
        from platform_support import AudioPlayer
        with tempfile.TemporaryDirectory() as temporary, patch('platform_support.SOUND_DIR', Path(temporary)), \
             patch('platform_support.winsound.PlaySound') as play_sound:
            player = AudioPlayer()
            tone_path = Path(temporary) / 'start.wav'
            tone_path.write_bytes(b'corrupted')
            player.play('start')
            with wave.open(str(tone_path), 'rb') as tone:
                self.assertGreater(tone.getnframes(), 0)
            play_sound.assert_called_once()

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

    def test_audio_player_caches_verified_stat_and_ignores_temp_cleanup_error(self):
        from platform_support import AudioPlayer
        with tempfile.TemporaryDirectory() as temporary, patch("platform_support.SOUND_DIR", Path(temporary)), \
             patch("platform_support.winsound.PlaySound"):
            player = AudioPlayer()
            with patch.object(AudioPlayer, "_valid_tone", return_value=True) as valid_tone:
                player.play("start")
                player.play("start")
            valid_tone.assert_not_called()
            with patch.object(Path, "unlink", side_effect=OSError("locked")):
                AudioPlayer._write_tone(Path(temporary) / "start.wav", AudioPlayer.SOUND_RECIPES["start"])


class RemoteTests(unittest.TestCase):
    def test_head_requests_return_headers_with_empty_body(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=2)
        try:
            conn.request("HEAD", "/")
            resp = conn.getresponse()
            body = resp.read()
            self.assertEqual(resp.status, 200)
            self.assertGreater(int(resp.getheader("Content-Length")), 0)
            self.assertEqual(body, b"")
        finally:
            conn.close()

    def test_incomplete_post_body_cannot_control_recording(self):
        import socket
        body = b'{"action":"start"}'
        with socket.create_connection(('127.0.0.1', self.server.port), timeout=2) as client:
            headers = (f'POST /api/control HTTP/1.1\r\nHost: localhost\r\nX-OBS-Token: {self.server.token}\r\n'
                       f'Content-Length: {len(body)+10}\r\n\r\n').encode('ascii')
            client.sendall(headers + body)
            client.shutdown(socket.SHUT_WR)
            self.assertIn(b'400', client.recv(4096).split(b'\r\n')[0])
        self.assertEqual(self.fake.requests, [])

    def test_confirmation_timeout_tracks_a_changed_frame_rate_budget(self):
        from unittest.mock import PropertyMock
        future = Future()
        future.set_running_or_notify_cancel()
        calls = []
        def budget():
            calls.append(True)
            return 0.03 if len(calls) == 1 else 0.5
        timer = threading.Timer(0.12, lambda: future.set_result({'ok':True,'state':self.controller.snapshot()}))
        timer.start()
        try:
            with patch.object(type(self.controller), 'control_timeout', new_callable=PropertyMock, side_effect=budget), \
                 patch.object(self.controller, 'submit', return_value=future):
                with self.request('/api/control', {'action':'start'}) as response:
                    self.assertTrue(json.load(response)['ok'])
        finally:
            timer.join()

    def test_control_race_between_timeout_and_completion_returns_success(self):
        class RacingFuture:
            def __init__(self, state):
                self._done = False
                self._state = state
            def done(self):
                return self._done
            def result(self, timeout=None):
                if not self._done:
                    self._done = True
                    raise TimeoutError()
                return {'ok': True, 'message': '开始录制成功', 'state': self._state}
        racing = RacingFuture(self.controller.snapshot())
        with patch.object(self.controller, 'submit', return_value=racing):
            with self.request('/api/control', {'action': 'start'}) as response:
                payload = json.load(response)
                self.assertTrue(payload['ok'])
                self.assertEqual(payload['message'], '开始录制成功')

    def test_control_future_timeout_exception_returns_409_with_message(self):
        future = Future()
        future.set_exception(TimeoutError('OBS 响应超时'))
        with patch.object(self.controller, 'submit', return_value=future):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/control', {'action': 'start'})
            self.assertEqual(error.exception.code, 409)
            self.assertEqual(json.loads(error.exception.read().decode())['message'], 'OBS 响应超时')

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

    def test_case_insensitive_same_origin_is_allowed(self):
        req = urllib.request.Request(
            f"{self.base}/api/status",
            headers={
                "Host": f"LOCALHOST:{self.server.port}",
                "Origin": f"http://localhost:{self.server.port}",
                "X-OBS-Token": self.server.token,
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as response:
            self.assertEqual(response.status, 200)

    def test_stopping_remote_unblocks_waiting_control_request_with_503(self):
        pending = Future()
        started = threading.Event()
        def slow_submit(action):
            started.set()
            return pending
        results = []
        def call_control():
            try:
                self.request("/api/control", {"action": "pause"})
            except urllib.error.HTTPError as exc:
                results.append(exc.code)
        with patch.object(self.controller, "submit", side_effect=slow_submit):
            worker = threading.Thread(target=call_control)
            worker.start()
            self.assertTrue(started.wait(2))
            self.server.stop()
            worker.join(timeout=3)
        self.assertEqual(results, [503])

    def test_same_origin_accepts_default_port_80_equivalence(self):
        for origin, host in (("http://192.168.1.10", "192.168.1.10:80"), ("http://192.168.1.10:80", "192.168.1.10")):
            with self.subTest(origin=origin, host=host):
                req = urllib.request.Request(
                    f"{self.base}/api/status",
                    headers={"Host": host, "Origin": origin, "X-OBS-Token": self.server.token},
                )
                with urllib.request.urlopen(req, timeout=2) as response:
                    self.assertEqual(response.status, 200)

    def test_non_json_content_type_post_is_rejected(self):
        with self.request("/api/status") as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        req = urllib.request.Request(
            f"{self.base}/api/control",
            data=b'{"action":"start"}',
            headers={"Cookie": cookie, "Content-Type": "text/plain"},
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(req, timeout=2)
        self.assertEqual(context.exception.code, 400)
        self.assertEqual(self.fake.requests, [])

    def test_api_responses_omit_last_file_path(self):
        self.fake.active = True
        with self.request("/api/status") as response:
            self.assertNotIn("last_file", json.load(response))
        with self.request("/api/control", {"action": "stop"}) as response:
            self.assertNotIn("last_file", json.load(response)["state"])
        self.assertEqual(self.controller.snapshot()["last_file"], "D:/recording.mkv")
        with patch.object(self.config, "save"):
            with self.request("/api/sound", {"enabled": False}) as response:
                self.assertNotIn("last_file", json.load(response)["state"])


if __name__ == "__main__":
    unittest.main()
