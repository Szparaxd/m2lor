"""Account lifecycle tests. Never starts a game or reads the user's store."""
import copy
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "recon"))
import autologin
import hub


class AccountFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = os.path.join(self.temp.name, "accounts.json")
        self.exe = os.path.join(self.temp.name, "GameCoreTop.exe")
        Path(self.exe).touch()
        self.store_patch = patch.object(autologin, "STORE_PATH", self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.path_patch = patch.object(autologin.settings, "game_path", return_value=self.exe)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.clients_patch = patch.object(autologin.clients, "list_clients", return_value=[])
        self.clients_patch.start()
        self.addCleanup(self.clients_patch.stop)
        self.messages = []
        self.manager = autologin.Manager(log=self.messages.append)

    def create(self, **changes):
        return self.manager.save_profile(dict(login="demo_account", password="demo-secret",
                                             nickname="Wojownik", channel=2, slot=1, radar=True, **changes))


class AccountTests(AccountFixture):
    def test_create_without_client_persists_encrypted_password_and_label(self):
        p = self.create()
        self.assertIsNone(p["pid"])
        self.assertEqual(p["nickname"], "Wojownik")
        self.assertTrue(p["has_password"])
        self.assertNotIn("pwd", p)
        self.assertNotIn("password", p)
        data = json.loads(Path(self.store).read_text(encoding="utf-8"))
        self.assertEqual(autologin.unprotect(data["profiles"][0]["pwd"]), "demo-secret")
        self.assertNotIn("demo-secret", Path(self.store).read_text(encoding="utf-8"))
        self.assertNotIn("demo-secret", " ".join(self.messages))
        self.assertEqual(autologin.Manager(log=lambda _: None).profiles()[0]["nickname"], "Wojownik")

    def test_edit_keeps_password_and_pid(self):
        p = self.manager.configure(42, {"login": "demo", "password": "demo-secret"})
        self.manager.save_profile({"nickname": "Nowa nazwa", "password": "", "channel": 4}, p["id"])
        stored = self.manager._profiles[0]
        self.assertEqual((stored["pid"], stored["login"], stored["channel"]), (42, "demo", 4))
        self.assertEqual(autologin.unprotect(stored["pwd"]), "demo-secret")

    def test_invalid_new_profile_does_not_leave_partial_account(self):
        with self.assertRaises(ValueError):
            self.manager.save_profile({"login": "demo"})
        self.assertEqual(self.manager.profiles(), [])
        self.assertFalse(os.path.exists(self.store))

    def test_login_change_without_new_password_does_not_modify_account(self):
        p = self.create()
        before = copy.deepcopy(self.manager._profiles)
        with self.assertRaises(ValueError):
            self.manager.save_profile({"login": "different", "password": ""}, p["id"])
        self.assertEqual(self.manager._profiles, before)
        self.assertEqual(json.loads(Path(self.store).read_text(encoding="utf-8"))["profiles"], before)

    def test_invalid_channel_slot_and_body_do_not_mutate_profiles(self):
        p = self.create()
        before = copy.deepcopy(self.manager._profiles)
        for body in ({"channel": 0}, {"slot": 4}, {"channel": "bad"}, {"slot": None},
                     {"radar": "false"}, {"channel": True}, [], {"login": "   "}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.manager.save_profile(body, p["id"])
            self.assertEqual(self.manager._profiles, before)

    def test_unknown_profile_does_not_create_account(self):
        with self.assertRaises(LookupError):
            self.manager.save_profile({"login": "demo", "password": "secret"}, "missing")
        self.assertEqual(self.manager.profiles(), [])

    def test_duplicate_login_and_slot_are_rejected(self):
        self.create()
        with self.assertRaises(ValueError):
            self.create()
        self.assertEqual(len(self.manager.profiles()), 1)

    def test_failed_disk_write_keeps_memory_consistent(self):
        p = self.create()
        before = copy.deepcopy(self.manager._profiles)
        with patch.object(self.manager, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.manager.save_profile({"nickname": "Different"}, p["id"])
        self.assertEqual(self.manager._profiles, before)

    def test_saved_restart_profile_does_not_start_before_first_launch(self):
        self.create(restart=True)
        with patch.object(self.manager, "_launch_client", side_effect=AssertionError("unexpected game launch")):
            self.manager._step(self.manager._profiles[0], set())
        self.assertEqual(self.manager.profiles()[0]["status"]["state"], "off")

    def test_launch_reports_progress_immediately_and_starts_only_once(self):
        p = self.create()
        self.manager.launch(p["id"])
        self.manager.launch(p["id"])
        self.assertEqual(self.manager.profiles()[0]["status"]["state"], "launch")
        with patch.object(self.manager, "_launch_client", return_value=99):
            self.manager._step(self.manager._profiles[0], set())
        self.assertEqual(self.manager.profiles()[0]["pid"], 99)
        self.assertFalse(self.manager._st(self.manager._profiles[0]).get("want_launch"))

    def test_retry_on_running_client_clears_fatal_error_without_new_game(self):
        p = self.manager.configure(42, {"login": "demo", "password": "secret"})
        self.manager._st(self.manager._profiles[0]).update(login_t=10, status={"state": "error", "text": "WRONGPWD"})
        with patch.object(autologin.clients, "list_clients", return_value=[(42, "GameCoreTop.exe")]):
            self.manager.launch(p["id"])
        st = self.manager._st(self.manager._profiles[0])
        self.assertNotIn("login_t", st)
        self.assertFalse(st.get("want_launch"))
        self.assertEqual(self.manager.profiles()[0]["pid"], 42)

    def test_missing_game_path_returns_actionable_error_before_queueing(self):
        p = self.create()
        os.remove(self.exe)
        with self.assertRaisesRegex(ValueError, "Ustawienia"):
            self.manager.launch(p["id"])
        self.assertFalse(self.manager._st(self.manager._profiles[0]).get("want_launch"))

    def test_failed_launch_error_survives_following_ticks(self):
        p = self.create()
        self.manager.launch(p["id"])
        profile = self.manager._profiles[0]
        with patch.object(self.manager, "_launch_client", return_value=None):
            self.manager._step(profile, set())
        self.assertEqual(self.manager.profiles()[0]["status"]["state"], "error")
        self.manager._step(profile, set())
        self.assertEqual(self.manager.profiles()[0]["status"]["state"], "error")

    def test_launch_ignores_prior_new_process_with_higher_pid(self):
        # Another account was launched since the worker captured its alive set.
        with patch.object(autologin.clients, "list_clients", side_effect=[[(202, "GameCoreTop.exe")],
                          [(201, "GameCoreTop.exe"), (202, "GameCoreTop.exe")]]), \
                patch.object(autologin.subprocess, "Popen", return_value=SimpleNamespace(pid=201)):
            self.assertEqual(self.manager._launch_client(set()), 201)

    def test_legacy_pid_configuration_uses_existing_profile(self):
        p = self.create()
        attached = self.manager.configure(42, {"login": "demo_account", "slot": 1, "password": ""})
        self.assertEqual(attached["id"], p["id"])
        self.assertEqual(attached["pid"], 42)
        self.assertEqual(attached["nickname"], "Wojownik")


class AccountHTTPTests(AccountFixture):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), hub.make_handler(SimpleNamespace(autologin=self.manager), 0))
        self.port = self.server.server_port
        self.server.RequestHandlerClass = hub.make_handler(SimpleNamespace(autologin=self.manager), self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, path, body, origin=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                         json.dumps(body).encode(), {"Content-Type": "application/json",
                                         "Origin": origin or f"http://127.0.0.1:{self.port}"})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            return response.code, json.load(response)

    def test_create_and_edit_http_contract_without_exposing_password(self):
        code, data = self.post("/api/autologin", {"login": "demo", "password": "secret", "nickname": "Test"})
        self.assertEqual(code, 200, data)
        p = data["profile"]
        self.assertTrue(p["has_password"])
        code, data = self.post(f"/api/autologin/{p['id']}/save", {"nickname": "Edited", "password": ""})
        self.assertEqual(code, 200, data)
        self.assertEqual(data["profile"]["nickname"], "Edited")
        self.assertNotIn("secret", json.dumps(data))

    def test_profile_save_rejects_foreign_origin(self):
        code, _ = self.post("/api/autologin", {"login": "demo", "password": "secret"}, "https://foreign.example")
        self.assertEqual(code, 403)
        self.assertEqual(self.manager.profiles(), [])

    def test_malformed_profile_body_returns_400(self):
        code, _ = self.post("/api/autologin", [])
        self.assertEqual(code, 400)

    def test_bulk_import_http_accepts_two_columns_without_launching(self):
        code, result = self.post('/api/autologin/import', {'text': 'alfa;secret-a\nbeta;secret-b'})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['added'], 2)
        self.assertFalse(self.manager.profiles()[0]['enabled'])
        self.assertNotIn('secret', json.dumps(result))

    def test_http_launch_passes_selected_channel_and_options(self):
        p = self.create()
        code, result = self.post(f'/api/autologin/{p["id"]}/launch', {'channel': 4, 'slot': 3, 'radar': False})
        self.assertEqual(code, 200, result)
        current = self.manager.profiles()[0]
        self.assertEqual((current['channel'], current['slot'], current['radar']), (4, 3, False))


if __name__ == "__main__":
    unittest.main()
