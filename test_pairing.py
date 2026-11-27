import json
import unittest
import urllib.error
import urllib.request
import uuid
import threading
from http.cookiejar import CookieJar
from unittest.mock import MagicMock, patch

import settings
from obs_controller import OBSController
from pairing import PairingIdentity
from remote_server import RemoteServer, PAIRING_COOKIE


class PairingIdentityTests(unittest.TestCase):
    def test_revoked_token_cannot_enter_a_mutation(self):
        from pairing import PairingRevokedError
        identity = PairingIdentity()
        old = identity.token
        identity.rotate()
        operation = MagicMock()
        with self.assertRaises(PairingRevokedError):
            identity.run_if_current(old, operation)
        operation.assert_not_called()

    def test_rotation_waits_for_an_already_authorized_mutation(self):
        identity = PairingIdentity()
        token = identity.token
        entered, release, rotating, rotated = (threading.Event() for _ in range(4))
        def operation():
            entered.set()
            release.wait(2)
        def rotation():
            rotating.set(); identity.rotate(); rotated.set()
        worker = threading.Thread(target=lambda: identity.run_if_current(token, operation))
        rotate = threading.Thread(target=rotation)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            rotate.start()
            self.assertTrue(rotating.wait(1))
            self.assertFalse(rotated.wait(0.05))
        finally:
            release.set(); worker.join(2)
            if rotate.ident is not None:rotate.join(2)
        self.assertTrue(rotated.is_set())

    def test_saved_identity_survives_desktop_restart(self):
        store = MagicMock()
        store.read.return_value = "A" * 43
        first = PairingIdentity(store=store)
        second = PairingIdentity(store=store)
        self.assertEqual(first.token, second.token)
        store.write.assert_not_called()

    def test_new_identity_is_saved_before_use(self):
        store = MagicMock()
        store.read.return_value = ""
        identity = PairingIdentity(store=store)
        self.assertGreaterEqual(len(identity.token), 32)
        store.write.assert_called_once_with(identity.token)

    def test_failed_rotation_preserves_existing_pairing(self):
        store = MagicMock()
        store.read.return_value = "A" * 43
        identity = PairingIdentity(store=store)
        store.write.side_effect = OSError("credential unavailable")
        with self.assertRaises(OSError):
            identity.rotate()
        self.assertEqual(identity.token, "A" * 43)

    def test_mobile_identity_uses_separate_credential_target(self):
        with patch("pairing.WindowsCredentialStore") as store:
            store.return_value.read.return_value = "A" * 43
            self.assertEqual(PairingIdentity.persistent().token, "A" * 43)
            store.assert_called_once_with(target=PairingIdentity.credential_target())

    def test_isolated_user_profiles_do_not_share_pairing_identity(self):
        from pathlib import Path
        self.assertNotEqual(PairingIdentity.credential_target(Path("profile-a")), PairingIdentity.credential_target(Path("profile-b")))

    def test_corrupted_credential_rebuilds_identity_automatically(self):
        store = MagicMock()
        store.read.side_effect = ValueError("corrupted credential")
        identity = PairingIdentity(store=store)
        self.assertGreaterEqual(len(identity.token), 32)
        store.write.assert_called_once_with(identity.token)

    def test_unavailable_credential_store_raises_without_overwriting(self):
        store = MagicMock()
        store.read.side_effect = OSError("credential store busy")
        with self.assertRaises(OSError):
            PairingIdentity(store=store)
        store.write.assert_not_called()


class PersistentBrowserPairingTests(unittest.TestCase):
    def test_reset_between_body_validation_and_dispatch_rejects_the_mutation(self):
        original = self.server.dispatch
        def reset_before_dispatch(token, operation):
            self.server.reset_pairing()
            return original(token, operation)
        with patch.object(self.server, 'dispatch', side_effect=reset_before_dispatch), \
             patch.object(self.controller, 'set_sound_enabled') as change:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/sound', token=True, data={'enabled': False})
            self.assertEqual(error.exception.code, 401)
            change.assert_not_called()

    def test_stop_between_body_validation_and_dispatch_rejects_the_mutation(self):
        original = self.server.dispatch
        def stop_before_dispatch(token, operation):
            self.server.stop()
            return original(token, operation)
        with patch.object(self.server, 'dispatch', side_effect=stop_before_dispatch), \
             patch.object(self.controller, 'set_sound_enabled') as change:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/sound', token=True, data={'enabled': False})
            self.assertEqual(error.exception.code, 503)
            change.assert_not_called()

    def test_stopping_remote_while_reading_body_prevents_late_mutation(self):
        handler_type = self.server.server.RequestHandlerClass
        authenticate = handler_type.authenticated
        def stop_after_auth(handler):
            result = authenticate(handler)
            if result:
                self.server.stop()
            return result
        with patch.object(handler_type, 'authenticated', stop_after_auth), \
             patch.object(self.controller, 'set_sound_enabled') as change:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/sound', token=True, data={'enabled': False})
            self.assertEqual(error.exception.code, 503)
            change.assert_not_called()

    def setUp(self):
        self.controller = OBSController(settings.AppConfig())
        self.identity = PairingIdentity()
        self.server = RemoteServer(self.controller, host="127.0.0.1", port=0, identity=self.identity)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.server.port}"
        self.jar = CookieJar()
        self.browser = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar))

    def tearDown(self):
        self.server.stop()

    def request(self, path="/api/status", token=False, data=None, origin=None, browser=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-OBS-Token"] = self.server.token
        if origin:
            headers["Origin"] = origin
        return (browser or self.browser).open(urllib.request.Request(self.base + path,
            headers=headers, data=json.dumps(data).encode() if data is not None else None), timeout=3)

    def test_first_pairing_creates_persistent_httponly_cookie(self):
        with self.request(token=True) as response:
            cookie = response.headers["Set-Cookie"]
        for part in ("Max-Age=31536000", "HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(part, cookie)
        self.assertFalse(next(iter(self.jar)).discard)

    def test_close_browser_and_reopen_root_without_token(self):
        with self.request(token=True):
            pass
        reopened = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar))
        with self.request(browser=reopened) as response:
            self.assertIn("instance_id", json.load(response))
            self.assertIsNone(response.headers.get("Set-Cookie"), "Polling should not rewrite persistent cookies every 0.8 seconds")

    def test_valid_cookie_controls_without_javascript_token(self):
        from concurrent.futures import Future
        with self.request(token=True):
            pass
        future = Future()
        future.set_result({"ok": True, "state": self.controller.snapshot()})
        with patch.object(self.controller, "submit", return_value=future) as submit:
            with self.request("/api/control", data={"action": "pause"}) as response:
                self.assertTrue(json.load(response)["ok"])
            submit.assert_called_once_with("pause")

    def test_desktop_restart_keeps_identity_but_changes_state_instance(self):
        with self.request(token=True) as response:
            old_instance = json.load(response)["instance_id"]
        self.server.stop()
        self.server = RemoteServer(OBSController(settings.AppConfig()), host="127.0.0.1", port=self.server.port, identity=self.identity)
        self.server.start()
        with self.request() as response:
            self.assertNotEqual(json.load(response)["instance_id"], old_instance)

    def test_reset_revokes_cookie_and_old_link(self):
        old_token = self.server.token
        with self.request(token=True):
            pass
        self.server.reset_pairing()
        self.assertNotEqual(self.server.token, old_token)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request()
        self.assertEqual(error.exception.code, 401)
        raw = urllib.request.Request(self.base + "/api/status", headers={"X-OBS-Token": old_token})
        with self.assertRaises(urllib.error.HTTPError):
            self.browser.open(raw, timeout=3)

    def test_rotation_during_response_cannot_issue_new_secret_to_old_device(self):
        original = self.controller.snapshot
        def rotate_while_producing_status():
            self.server.reset_pairing()
            return original()
        with patch.object(self.controller, "snapshot", side_effect=rotate_while_producing_status):
            with self.request(token=True) as response:
                self.assertIsNone(response.headers.get("Set-Cookie"))

    def test_reset_during_post_body_read_revokes_the_request_before_mutation(self):
        handler_type = self.server.server.RequestHandlerClass
        authenticate = handler_type.authenticated
        def rotate_after_auth(handler):
            result = authenticate(handler)
            if result:
                self.server.reset_pairing()
            return result
        with patch.object(handler_type, 'authenticated', rotate_after_auth), \
             patch.object(self.controller, 'set_sound_enabled') as change:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/sound', token=True, data={'enabled': False})
            self.assertEqual(error.exception.code, 401)
            change.assert_not_called()

    def test_forget_removes_cookie_and_no_recording_request_is_sent(self):
        with self.request(token=True):
            pass
        with patch.object(self.controller, "submit") as submit:
            with self.request("/api/unpair", data={}) as response:
                self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
            submit.assert_not_called()
        self.assertFalse(list(self.jar))
        with self.assertRaises(urllib.error.HTTPError):
            self.request()

    def test_cross_origin_cannot_use_saved_cookie(self):
        with self.request(token=True):
            pass
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/control", data={"action": "start"}, origin="http://unrelated.example")
        self.assertEqual(error.exception.code, 403)

    def test_malformed_cookie_is_rejected_cleanly(self):
        raw = urllib.request.Request(self.base + "/api/status", headers={"Cookie": f'{PAIRING_COOKIE}="invalid'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.browser.open(raw, timeout=3)
        self.assertEqual(error.exception.code, 401)

    def test_failed_response_never_sets_renewal_cookie(self):
        with self.assertRaises(urllib.error.HTTPError) as not_found:
            self.request("/api/nonexistent", token=True, data={})
        self.assertEqual(not_found.exception.code, 404)
        self.assertIsNone(not_found.exception.headers.get("Set-Cookie"))
        self.assertFalse(list(self.jar))

    def test_remote_control_unblocks_with_401_if_pairing_reset_while_waiting(self):
        from concurrent.futures import Future
        pending = Future()
        started = threading.Event()
        def slow_submit(action):
            started.set()
            return pending
        with patch.object(self.controller, "submit", side_effect=slow_submit):
            def reset_soon():
                if started.wait(2):
                    self.server.reset_pairing()
            worker = threading.Thread(target=reset_soon)
            worker.start()
            try:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request("/api/control", token=True, data={"action": "pause"})
                self.assertEqual(error.exception.code, 401)
            finally:
                worker.join(2)

    def test_remote_control_cancellation_race_returns_200_when_future_completes(self):
        from unittest.mock import PropertyMock
        snapshot = self.controller.snapshot()
        class LateCancelFuture:
            def __init__(self):
                self._done = False
            def result(self, timeout=None):
                if not self._done:
                    raise TimeoutError()
                return {"ok": True, "message": "开始录制成功", "state": snapshot}
            def done(self):
                return self._done
            def cancel(self):
                self._done = True
                return False
        with patch.object(type(self.controller), "control_timeout", new_callable=PropertyMock, return_value=0.02), \
             patch.object(self.controller, "submit", return_value=LateCancelFuture()):
            with self.request("/api/control", token=True, data={"action": "start"}) as response:
                payload = json.load(response)
                self.assertTrue(payload["ok"])
                self.assertNotIn("last_file", payload["state"])


class SingleInstanceTests(unittest.TestCase):
    def test_second_instance_activates_existing_window_and_closing_releases_name(self):
        from PySide6.QtWidgets import QApplication
        from instance_guard import SingleInstance
        app = QApplication.instance() or QApplication([])
        name = "obs-monitor-test-" + uuid.uuid4().hex
        first, second = SingleInstance(name), SingleInstance(name)
        activated = []
        first.activate.connect(lambda: activated.append(True))
        try:
            self.assertTrue(first.claim())
            self.assertFalse(second.claim())
            for _ in range(10):
                app.processEvents()
            self.assertEqual(activated, [True])
            first.close()
            self.assertTrue(second.claim())
        finally:
            first.close(); second.close()


if __name__ == "__main__":
    unittest.main()
