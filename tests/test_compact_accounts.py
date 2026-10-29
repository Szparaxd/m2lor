"""Credential-only entry, atomic bulk import and per-launch choices."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

from test_accounts import AccountFixture
import autologin


class CompactAccountTests(AccountFixture):
    def test_credentials_only_reject_duplicate_login_from_different_character_slot(self):
        self.create()
        with self.assertRaises(ValueError):
            self.manager.save_profile({'login': 'demo_account', 'password': 'replacement'})
        self.assertEqual(len(self.manager.profiles()), 1)
        self.assertEqual(autologin.unprotect(self.manager._profiles[0]['pwd']), 'demo-secret')

    def test_import_ignores_empty_spreadsheet_rows(self):
        result = self.manager.import_credentials('  \nalfa\tsecret\n\t\nbeta\tother\n')
        self.assertEqual(result['added'], 2)

    def test_credentials_only_are_saved_without_enabling_game_automation(self):
        p = self.manager.save_profile({"login": "simple", "password": "secret"})
        self.assertFalse(p["enabled"])
        self.assertFalse(p["radar"])
        self.assertFalse(p["restart"])
        self.assertIsNone(p["pid"])

    def test_import_semicolon_csv_and_preserve_quoted_password(self):
        result = self.manager.import_credentials('login;haslo\nalfa;"secret;with;separator"\nbeta;other-secret\n')
        self.assertEqual((result["added"], result["skipped"]), (2, 0))
        self.assertEqual([p["login"] for p in self.manager.profiles()], ["alfa", "beta"])
        self.assertEqual(autologin.unprotect(self.manager._profiles[0]["pwd"]), "secret;with;separator")
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("other-secret", Path(self.store).read_text(encoding="utf-8"))

    def test_import_tab_and_comma_formats(self):
        for text in ('alfa\tpass-a\nbeta\tpass-b', 'alfa,"pass,with,commas"\nbeta,pass-b'):
            with self.subTest(text=text):
                self.manager._profiles.clear()
                result = self.manager.import_credentials(text)
                self.assertEqual(result["added"], 2)
                self.assertEqual([p["login"] for p in self.manager.profiles()], ["alfa", "beta"])

    def test_password_punctuation_is_never_misread_as_login(self):
        for text, password in [('alfa;pass,word', 'pass,word'), ('alfa\tpass,word', 'pass,word'),
                               ('alfa,pass;word', 'pass;word')]:
            with self.subTest(text=text):
                self.manager._profiles.clear()
                self.manager.import_credentials(text)
                self.assertEqual(self.manager.profiles()[0]['login'], 'alfa')
                self.assertEqual(autologin.unprotect(self.manager._profiles[0]['pwd']), password)

    def test_launch_with_bot_disabled_stops_existing_worker(self):
        worker = {'running': True}
        def stop_worker(pid):
            self.assertEqual(pid, 42)
            worker['running'] = False
            return 200, {'ok': True}
        self.manager = autologin.Manager(log=self.messages.append, radar_stop=stop_worker)
        p = self.manager.configure(42, {'login': 'demo', 'password': 'secret', 'radar': True})
        with patch.object(autologin.clients, 'list_clients', return_value=[(42, 'GameCoreTop.exe')]), \
             patch.object(autologin, 'game_logout', return_value={'sent': 1}):
            self.manager.launch(p['id'], {'channel': 2, 'radar': False, 'relog': True})
        self.assertFalse(worker['running'])
        self.assertFalse(self.manager.profiles()[0]['radar'])

    def test_import_skips_existing_and_duplicate_logins_without_changing_password(self):
        old = self.manager.save_profile({"login": "old", "password": "original"})
        result = self.manager.import_credentials('old;replacement\nnew;first\nnew;second')
        self.assertEqual((result["added"], result["skipped"]), (1, 2))
        self.assertEqual(autologin.unprotect(self.manager._profiles[0]["pwd"]), "original")
        self.assertEqual(self.manager.profiles()[0]["id"], old["id"])
        self.assertEqual(autologin.unprotect(self.manager._profiles[1]["pwd"]), "first")

    def test_invalid_import_is_atomic_and_errors_do_not_echo_credentials(self):
        self.create()
        before = copy.deepcopy(self.manager._profiles)
        for text in ('new;secret\nbroken', 'new;secret\nother;', 'new;"unterminated-secret', '', []):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as error:
                    self.manager.import_credentials(text)
                self.assertNotIn('secret', str(error.exception))
                self.assertEqual(self.manager._profiles, before)

    def test_failed_import_disk_write_does_not_leave_added_accounts(self):
        self.create()
        before = copy.deepcopy(self.manager._profiles)
        with patch.object(self.manager, '_save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.manager.import_credentials('new;secret')
        self.assertEqual(self.manager._profiles, before)

    def test_launch_applies_options_and_keeps_encrypted_credentials(self):
        p = self.manager.save_profile({"login": "simple", "password": "secret"})
        self.manager.launch(p["id"], {"channel": 4, "slot": 2, "radar": True, "restart": True})
        current = self.manager.profiles()[0]
        self.assertEqual((current["channel"], current["slot"], current["radar"], current["restart"]), (4, 2, True, True))
        self.assertTrue(current["enabled"])
        self.assertEqual(autologin.unprotect(self.manager._profiles[0]["pwd"]), "secret")
        self.assertEqual(current["status"]["state"], 'launch')

    def test_invalid_launch_options_do_not_change_profile_or_queue_game(self):
        p = self.create()
        before = copy.deepcopy(self.manager._profiles)
        with self.assertRaises(ValueError):
            self.manager.launch(p["id"], {"channel": 7, "radar": True})
        self.assertEqual(self.manager._profiles, before)
        self.assertFalse(self.manager._st(self.manager._profiles[0]).get('want_launch'))

    def test_relog_applies_choices_to_existing_client_without_queueing_new_game(self):
        p = self.manager.configure(42, {"login": "simple", "password": "secret"})
        with patch.object(autologin.clients, 'list_clients', return_value=[(42, 'GameCoreTop.exe')]), \
             patch.object(autologin, 'game_logout', return_value={"sent": 1}):
            self.manager.launch(p['id'], {"channel": 3, "slot": 1, "relog": True})
        current = self.manager.profiles()[0]
        self.assertEqual((current['pid'], current['channel'], current['slot']), (42, 3, 1))
        self.assertFalse(self.manager._st(self.manager._profiles[0]).get('want_launch'))
