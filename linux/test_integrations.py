"""D-Bus contract tests against a private, deterministic desktop service."""
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

import desktop
from gi.repository import Gio, GLib, Gtk
from test_blackout import drain

XML = '''<node>
<interface name="org.freedesktop.portal.GlobalShortcuts">
 <method name="CreateSession"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
 <method name="BindShortcuts"><arg type="o" direction="in"/><arg type="a(sa{sv})" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
 <signal name="Activated"><arg type="o"/><arg type="s"/><arg type="t"/><arg type="a{sv}"/></signal>
</interface>
<interface name="org.freedesktop.portal.Inhibit">
 <method name="CreateMonitor"><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
 <method name="Inhibit"><arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
 <method name="QueryEndResponse"><arg type="o" direction="in"/></method>
 <signal name="StateChanged"><arg type="o"/><arg type="a{sv}"/></signal>
</interface></node>'''
CLOSE_XML = '''<node>
<interface name="org.freedesktop.portal.Request"><method name="Close"/>
<signal name="Response"><arg type="u"/><arg type="a{sv}"/></signal></interface>
<interface name="org.freedesktop.portal.Session"><method name="Close"/></interface></node>'''
LOCK_XML = '''<node><interface name="org.freedesktop.ScreenSaver">
<signal name="ActiveChanged"><arg type="b"/></signal></interface></node>'''


class Contracts(unittest.TestCase):
    def setUp(self):
        self.daemon = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--print-address=1', '--nosyslog'],
                                       stdout=subprocess.PIPE, text=True)
        address = self.daemon.stdout.readline().strip()
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        self.server = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        self.client = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        for name in (desktop.PORTAL, 'org.freedesktop.ScreenSaver'):
            self.server.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                'RequestName', GLib.Variant('(su)', (name, 0)), None, Gio.DBusCallFlags.NONE, 3000, None)
        self.registrations = []
        for interface in Gio.DBusNodeInfo.new_for_xml(XML).interfaces:
            self.registrations.append(self.server.register_object(desktop.PORTAL_PATH, interface, self.method, None, None))
        self.registrations.append(self.server.register_object('/ScreenSaver',
            Gio.DBusNodeInfo.new_for_xml(LOCK_XML).interfaces[0], None, None, None))
        self.calls, self.closed, self.sessions = [], [], {}
        self.deny = False
        self.fail_inhibit = False
        self.app = types.SimpleNamespace(settings={'lock': True}, active=False, stops=0,
            integration_error='', menu=Gtk.Menu(), display=types.SimpleNamespace(__gtype__=types.SimpleNamespace(name='FakeDisplay')),
            get_dbus_connection=lambda: self.client, integrations_changed=lambda: None)
        def toggle():
            self.app.active = not self.app.active
        def stop():
            self.app.active = False
            self.app.stops += 1
        self.app.toggle, self.app.stop = toggle, stop
        self.tray_patch = patch.object(desktop, 'Tray', side_effect=ValueError('no test tray'))
        self.tray_patch.start()
        self.environment = patch.dict('os.environ', {'XDG_CURRENT_DESKTOP': ''})
        self.environment.start()
        self.integration = desktop.Desktop(self.app)
        drain(0.1)

    def tearDown(self):
        self.integration.close()
        drain(0.05)
        self.assertFalse(self.integration.portal.requests)
        self.assertFalse(self.integration.portal.sessions)
        self.tray_patch.stop()
        self.environment.stop()
        self.client.close_sync(None)
        self.server.close_sync(None)
        self.daemon.terminate()
        self.daemon.wait(timeout=3)
        self.daemon.stdout.close()

    def register_handle(self, path, kind):
        interface = next(i for i in Gio.DBusNodeInfo.new_for_xml(CLOSE_XML).interfaces if i.name.endswith(kind))
        self.registrations.append(self.server.register_object(path, interface, self.method, None, None))

    def method(self, _bus, _sender, path, interface, method, parameters, invocation):
        args = parameters.unpack()
        self.calls.append((method, args))
        if method == 'Close':
            self.closed.append(path)
            invocation.return_value(None)
            return
        if method == 'QueryEndResponse':
            invocation.return_value(None)
            return
        if method == 'Inhibit' and self.fail_inhibit:
            invocation.return_dbus_error('org.freedesktop.portal.Error.NotAllowed', 'Test inhibition denied')
            return
        options = args[-1]
        token = options.get('handle_token', 'inhibit_' + str(len(self.calls)))
        handle = desktop.PORTAL_PATH + '/request/' + self.client.get_unique_name()[1:].replace('.', '_') + '/' + token
        self.register_handle(handle, 'Request')
        results = {}
        if method in ('CreateSession', 'CreateMonitor'):
            session = desktop.PORTAL_PATH + '/session/test/' + options['session_handle_token']
            self.register_handle(session, 'Session')
            self.sessions[method] = session
            results['session_handle'] = GLib.Variant('s', session)
        elif method == 'BindShortcuts':
            results['shortcuts'] = GLib.Variant('a(sa{sv})', [('toggle', {'trigger_description': GLib.Variant('s', 'Ctrl+Alt+B')})])
        # Respond before the method reply, exercising the request-subscription race.
        if method != 'Inhibit':
            self.server.emit_signal(None, handle, 'org.freedesktop.portal.Request', 'Response',
                                    GLib.Variant('(ua{sv})', (1 if self.deny else 0, results)))
        invocation.return_value(GLib.Variant('(o)', (handle,)))

    def test_shortcut_registration_and_activation(self):
        self.assertEqual(self.integration.shortcut.state, 'Ctrl+Alt+B')
        bind = next(args for method, args in self.calls if method == 'BindShortcuts')
        self.assertEqual(bind[1][0][1]['preferred_trigger'], 'CTRL+ALT+b')
        session = self.sessions['CreateSession']
        for expected in (True, False):
            self.server.emit_signal(None, desktop.PORTAL_PATH, 'org.freedesktop.portal.GlobalShortcuts', 'Activated',
                                    GLib.Variant('(osta{sv})', (session, 'toggle', 1, {})))
            drain(0.05)
            self.assertEqual(self.app.active, expected)

    def test_lock_toggle_and_portal_session_filter(self):
        for enabled in (False, True):
            self.app.settings['lock'] = enabled
            self.app.active = True
            self.server.emit_signal(None, '/ScreenSaver', 'org.freedesktop.ScreenSaver', 'ActiveChanged',
                                    GLib.Variant('(b)', (True,)))
            drain(0.05)
            self.assertEqual(self.app.active, not enabled)
        self.app.active = True
        session = self.sessions['CreateMonitor']
        for path, expected in [('/unrelated/session', True), (session, False)]:
            self.server.emit_signal(None, desktop.PORTAL_PATH, 'org.freedesktop.portal.Inhibit', 'StateChanged',
                GLib.Variant('(oa{sv})', (path, {'screensaver-active': GLib.Variant('b', True)})))
            drain(0.05)
            self.assertEqual(self.app.active, expected)

    def test_keep_awake_release_and_pending_request_race(self):
        self.integration.keep_awake(True)
        drain(0.05)
        handle = self.integration.inhibition
        self.assertTrue(handle)
        inhibit = next(args for method, args in self.calls if method == 'Inhibit')
        self.assertEqual(inhibit[1], 12)
        self.integration.keep_awake(False)
        drain(0.05)
        self.assertIn(handle, self.closed)
        self.assertIsNone(self.integration.inhibition)
        self.integration.keep_awake(True)
        self.integration.keep_awake(False)
        drain(0.05)
        self.assertIsNone(self.integration.inhibition)
        self.assertEqual(len([c for c in self.calls if c[0] == 'Inhibit']), 2)
        self.assertEqual(len(self.closed), 2)

    def test_inhibition_failure_is_reported(self):
        self.fail_inhibit = True
        self.integration.keep_awake(True)
        drain(0.05)
        self.assertIsNone(self.integration.inhibition)
        self.assertIn('Keep-awake unavailable', self.app.integration_error)

    def test_denied_shortcut_is_not_claimed_registered(self):
        self.deny = True
        self.integration.shortcut.close()
        self.integration.shortcut = desktop.Shortcuts(self.integration.portal, self.app.toggle, lambda: None)
        drain(0.1)
        self.assertIn('unavailable', self.integration.shortcut.state)

    def test_shutdown_closes_inhibition_and_sessions(self):
        self.integration.keep_awake(True)
        drain(0.05)
        sessions = set(self.integration.portal.sessions)
        inhibitor = self.integration.inhibition
        self.integration.close()
        # Avoid double cleanup by tearDown.
        self.integration.close = lambda: None
        drain(0.05)
        self.assertTrue(sessions.issubset(self.closed))
        self.assertIn(inhibitor, self.closed)


if __name__ == '__main__':
    unittest.main(verbosity=2)
