"""Behavioral regression tests. Run in an isolated D-Bus session and Xvfb."""
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import blackout
import desktop
import updater
from gi.repository import Gdk, Gio, GLib, Gtk


def drain(seconds=0):
    deadline = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while True:
        while context.pending():
            context.iteration(False)
        if time.monotonic() >= deadline:
            break
        time.sleep(0.005)


class Behavior(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='blackout tests ')
        cls.config = patch.object(blackout, 'CONFIG', Path(cls.temporary.name) / 'settings.json')
        cls.config.start()
        cls.login = patch.object(desktop, 'AUTOSTART', Path(cls.temporary.name) / 'autostart/blackout.desktop')
        cls.login.start()
        cls.app = blackout.Blackout(integrations=False, application_id='io.github.tacoz234.Blackout.Test')
        assert cls.app.register(None)

    @classmethod
    def tearDownClass(cls):
        cls.app.stop()
        if cls.app.controls:
            cls.app.controls.destroy()
        cls.app.release()
        cls.config.stop()
        cls.login.stop()
        cls.temporary.cleanup()

    def setUp(self):
        self.app.stop()
        self.app.settings.update(excluded=[], minutes=0, awake=False, input=False, clock=False, clock24=False, lock=True)

    def test_resident_after_stop_and_options_close(self):
        self.app.preferences()
        self.app.start()
        drain(0.05)
        self.assertTrue(self.app.covers)
        self.assertFalse(self.app.controls.get_visible())
        self.app.stop()
        self.assertFalse(self.app.covers)
        self.assertFalse(self.app.controls.get_visible())
        self.app.preferences()
        self.assertTrue(self.app.close_controls(self.app.controls, None))
        self.assertFalse(self.app.controls.get_visible())
        self.app.start()
        self.assertTrue(self.app.covers)

    def test_all_monitors_and_no_selection_fallback(self):
        self.app.start()
        self.assertEqual(len(self.app.covers), self.app.display.get_n_monitors())
        self.app.stop()
        self.app.settings['excluded'] = [self.app.monitor_key(m, i) for i, m in enumerate(self.app.monitors())]
        self.app.start()
        self.assertEqual(len(self.app.covers), self.app.display.get_n_monitors())

    def test_partial_display_does_not_request_focus(self):
        monitors = self.app.monitors()
        if len(monitors) < 2:
            # Xvfb has one output; model selection of output 0 while output 1 is excluded.
            with patch.object(self.app, 'monitors', return_value=monitors * 2), patch.object(self.app, 'monitor_key', side_effect=lambda m, i: str(i)):
                self.app.settings['excluded'] = ['1']
                self.app.start()
        else:
            self.app.settings['excluded'] = [self.app.monitor_key(monitors[0], 0)]
            self.app.start()
        self.assertEqual(len(self.app.covers), max(1, len(monitors) - 1))
        self.assertTrue(all(not window.get_focus_on_map() for window in self.app.covers))

    def test_escape_any_key_click_and_alt_f4(self):
        for action in ('escape', 'any', 'click', 'close'):
            self.app.start()
            window = self.app.covers[0]
            self.app.settings['input'] = action in ('any', 'click')
            if action in ('escape', 'any'):
                window.key_pressed(window, types.SimpleNamespace(keyval=Gdk.KEY_Escape if action == 'escape' else Gdk.KEY_a))
            elif action == 'click':
                window.clicked(window, None)
            else:
                self.assertTrue(window.end_event())
            self.assertFalse(self.app.covers, action)

    def test_input_motion_threshold_and_startup_grace(self):
        self.app.settings['input'] = True
        self.app.start()
        window = self.app.covers[0]
        window.motion(window, types.SimpleNamespace(x_root=10, y_root=10))
        window.motion(window, types.SimpleNamespace(x_root=100, y_root=100))
        self.assertTrue(self.app.covers)
        self.app.started_at -= 2
        window.motion(window, types.SimpleNamespace(x_root=102, y_root=102))
        self.assertTrue(self.app.covers)
        window.motion(window, types.SimpleNamespace(x_root=110, y_root=110))
        self.assertFalse(self.app.covers)

    def test_clock_on_only_moving_screen_fades_to_pure_black(self):
        self.app.settings['clock'] = True
        self.app.start()
        self.app.started_at -= 2
        window = self.app.covers[-1]
        window.motion(window, types.SimpleNamespace(x_root=0, y_root=0))
        window.motion(window, types.SimpleNamespace(x_root=20, y_root=20))
        self.assertRegex(window.overlay.get_text(), r'^\d{1,2}:\d{2} (AM|PM)$')
        self.assertTrue(window.revealer.get_reveal_child())
        drain(3.2)
        self.assertFalse(window.revealer.get_reveal_child())
        if self.app.display.__gtype__.name == 'GdkX11Display':
            pixbuf = Gdk.pixbuf_get_from_window(window.get_window(), 0, 0, window.get_allocated_width(), window.get_allocated_height())
            self.assertIsNotNone(pixbuf)
            self.assertEqual(set(pixbuf.get_pixels()), {0})

    def test_clock_format_boundaries_and_saved_choice(self):
        for hour, minute, twelve, twenty_four in [
            (0, 0, '12:00 AM', '00:00'), (0, 5, '12:05 AM', '00:05'),
            (1, 5, '1:05 AM', '01:05'), (11, 59, '11:59 AM', '11:59'),
            (12, 0, '12:00 PM', '12:00'), (12, 5, '12:05 PM', '12:05'),
            (13, 5, '1:05 PM', '13:05'), (23, 59, '11:59 PM', '23:59'),
        ]:
            now = time.struct_time((2026, 1, 1, hour, minute, 0, 3, 1, -1))
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(blackout.format_clock(now, False), twelve)
                self.assertEqual(blackout.format_clock(now, True), twenty_four)
        for enabled in (True, False):
            self.app.boolean_items['clock24'].set_active(enabled)
            self.assertEqual(self.app.settings['clock24'], enabled)
            self.assertEqual(blackout.Blackout(integrations=False).settings['clock24'], enabled)

    def test_clock_overlay_uses_selected_format(self):
        self.app.settings['clock'] = True
        self.app.start()
        self.app.started_at -= 2
        window = self.app.covers[0]
        window.motion(window, types.SimpleNamespace(x_root=0, y_root=0))
        now = time.struct_time((2026, 1, 1, 13, 5, 0, 3, 1, -1))
        with patch.object(blackout.time, 'localtime', return_value=now):
            for enabled, position, expected in [(False, 20, '1:05 PM'), (True, 40, '13:05')]:
                self.app.boolean_items['clock24'].set_active(enabled)
                window.motion(window, types.SimpleNamespace(x_root=position, y_root=position))
                self.assertEqual(window.overlay.get_text(), expected)

    def test_timer_reset_and_cleanup(self):
        self.app.settings['minutes'] = 15
        self.app.start()
        first = self.app.auto_exit
        self.assertIsNotNone(first)
        self.app.set_minutes(None, 30)
        self.assertNotEqual(first, self.app.auto_exit)
        self.assertIsNone(GLib.MainContext.default().find_source_by_id(first))
        windows = list(self.app.covers)
        self.app.stop()
        self.assertIsNone(self.app.auto_exit)
        self.assertTrue(all(not window.timers for window in windows))
        self.app.start()
        GLib.source_remove(self.app.auto_exit)
        self.app.auto_stop()
        self.assertFalse(self.app.covers)

    def test_auto_exit_fires_and_remains_resident(self):
        self.app.settings['minutes'] = 15
        delays = []
        def accelerated(seconds, callback):
            delays.append(seconds)
            return GLib.timeout_add(30, callback)
        with patch.object(GLib, 'timeout_add_seconds', side_effect=accelerated):
            self.app.start()
            drain(0.1)
        self.assertEqual(delays, [900])
        self.assertFalse(self.app.covers)
        self.assertIsNone(self.app.auto_exit)
        self.app.settings['minutes'] = 0
        self.app.start()
        self.assertTrue(self.app.covers)

    def test_monitor_change_rebuilds_active_blackout(self):
        self.app.start()
        old = list(self.app.covers)
        self.app.monitors_changed()
        self.assertTrue(self.app.covers)
        self.assertTrue(all(window not in old for window in self.app.covers))
        self.assertTrue(all(not window.timers for window in old))

    def test_settings_and_login_paths_with_spaces(self):
        self.app.settings['clock'] = True
        self.app.save()
        self.assertEqual(blackout.Blackout(integrations=False).settings, self.app.settings)
        desktop.set_autostart(True, '/tmp/space $`%/blackout.py')
        content = desktop.AUTOSTART.read_text()
        self.assertIn('--background', content)
        self.assertIn('\\$\\`%%', content)
        desktop.set_autostart(False, blackout.SCRIPT)
        self.assertFalse(desktop.AUTOSTART.exists())

    def test_real_escape_event(self):
        if self.app.display.__gtype__.name != 'GdkX11Display':
            self.skipTest('X11 backend required')
        self.app.start()
        drain(0.05)
        result = subprocess.check_output(['xdotool', 'search', '--name', '^Blackout$'], text=True)
        identity = result.splitlines()[0]
        subprocess.run(['xdotool', 'windowfocus', identity], check=True)
        subprocess.run(['xdotool', 'key', 'Escape'], check=True)
        drain(0.1)
        self.assertFalse(self.app.covers)

    def test_shortcut_real_x11_key_and_conflict(self):
        if self.app.display.__gtype__.name != 'GdkX11Display':
            self.skipTest('X11 backend required')
        shortcut = desktop.X11Shortcut(self.app.toggle)
        try:
            with self.assertRaisesRegex(RuntimeError, 'already in use'):
                desktop.X11Shortcut(self.app.toggle)
            subprocess.run(['xdotool', 'key', 'ctrl+alt+b'], check=True)
            drain(0.1)
            self.assertTrue(self.app.covers)
            subprocess.run(['xdotool', 'key', 'ctrl+alt+b'], check=True)
            drain(0.1)
            self.assertFalse(self.app.covers)
        finally:
            shortcut.close()


class Updates(unittest.TestCase):
    def archive(self, version='9.0.0', malicious=False):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            for filename in updater.FILES:
                contents = (version + '\n' if filename == 'VERSION' else '# release\n').encode()
                member = tarfile.TarInfo('linux/' + filename)
                member.size = len(contents)
                if malicious and filename == 'blackout.py':
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/etc/passwd'
                archive.addfile(member, io.BytesIO(contents))
        return buffer.getvalue()

    def test_find_newer_and_require_digest(self):
        release = {'tag_name': 'v9.0.0', 'assets': [{'name': 'Blackout-9.0.0-linux.tar.gz',
            'browser_download_url': 'https://example.com/app', 'digest': 'sha256:' + 'a' * 64}]}
        with patch.object(updater, 'fetch', return_value=json.dumps(release).encode()):
            self.assertEqual(updater.find_newer()['version'], '9.0.0')
        release['assets'][0]['digest'] = None
        with patch.object(updater, 'fetch', return_value=json.dumps(release).encode()):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                updater.find_newer()

    def test_verified_update_and_reject_corruption_unsafe_members(self):
        import hashlib
        for mode in ('valid', 'corrupt', 'symlink', 'wrong-version'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / 'blackout'
                destination.mkdir()
                (destination / 'VERSION').write_text('1.0.0')
                data = self.archive(version='8.0.0' if mode == 'wrong-version' else '9.0.0', malicious=mode == 'symlink')
                release = {'version': '9.0.0', 'url': 'https://example.com/app',
                           'sha256': '0' * 64 if mode == 'corrupt' else hashlib.sha256(data).hexdigest()}
                with patch.object(updater, 'fetch', return_value=data):
                    if mode == 'valid':
                        updater.install(release, destination)
                        self.assertEqual((destination / 'VERSION').read_text().strip(), '9.0.0')
                    else:
                        with self.assertRaises(ValueError):
                            updater.install(release, destination)
                        self.assertEqual((destination / 'VERSION').read_text(), '1.0.0')

    def test_rollback_when_replacement_fails(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'blackout'
            destination.mkdir()
            (destination / 'VERSION').write_text('1.0.0')
            data = self.archive()
            release = {'version': '9.0.0', 'url': 'https://example.com/app', 'sha256': hashlib.sha256(data).hexdigest()}
            original = Path.rename
            def rename(path, target):
                if path.name == 'new':
                    raise OSError('Simulated disk failure')
                return original(path, target)
            with patch.object(updater, 'fetch', return_value=data), patch.object(Path, 'rename', rename):
                with self.assertRaises(OSError):
                    updater.install(release, destination)
            self.assertEqual((destination / 'VERSION').read_text(), '1.0.0')


if __name__ == '__main__':
    unittest.main(verbosity=2)
