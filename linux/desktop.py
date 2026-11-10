"""Event-driven Linux desktop integration (no input or lock polling)."""
import ctypes as C
import ctypes.util
from pathlib import Path
import os
import sys
import uuid

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, Gtk

APP_ID = 'io.github.tacoz234.Blackout'
PORTAL = 'org.freedesktop.portal.Desktop'
PORTAL_PATH = '/org/freedesktop/portal/desktop'
DATA_HOME = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
CONFIG_HOME = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
AUTOSTART = CONFIG_HOME / 'autostart' / (APP_ID + '.desktop')


def desktop_exec(args):
    def quote(arg):
        return '"' + str(arg).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
    return ' '.join(map(quote, args))


def set_autostart(enabled, script):
    if enabled:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text('[Desktop Entry]\nType=Application\nName=Blackout\n'
                             f'Exec={desktop_exec([sys.executable, script, "--background"])}\n'
                             'Icon=weather-clear-night\nTerminal=false\nX-GNOME-Autostart-enabled=true\n')
    else:
        AUTOSTART.unlink(missing_ok=True)


class Portal:
    def __init__(self, bus):
        self.bus = bus
        self.requests = {}
        self.sessions = set()
        self.closed = False

    def request(self, interface, method, args, signature, done, session=False):
        token = 'blackout_' + uuid.uuid4().hex
        options = {'handle_token': GLib.Variant('s', token)}
        if session:
            options['session_handle_token'] = GLib.Variant('s', token + '_session')
        path = PORTAL_PATH + '/request/' + self.bus.get_unique_name()[1:].replace('.', '_') + '/' + token

        def response(_bus, _sender, _path, _interface, _signal, params):
            code, results = params.unpack()
            cleanup()
            if code == 0 and 'session_handle' in results:
                self.sessions.add(results['session_handle'])
            done(code, results)

        subscription = self.bus.signal_subscribe(PORTAL, 'org.freedesktop.portal.Request', 'Response', path, None,
                                                 Gio.DBusSignalFlags.NONE, response)
        def cleanup():
            entry = self.requests.pop(path, None)
            if entry:
                self.bus.signal_unsubscribe(subscription)
                GLib.source_remove(entry[1])

        def timed_out():
            entry = self.requests.pop(path, None)
            if entry:
                self.bus.signal_unsubscribe(subscription)
                self.close_object(path, 'Request')
                done(2, {'error': 'Desktop permission request timed out'})
            return GLib.SOURCE_REMOVE

        self.requests[path] = (subscription, GLib.timeout_add_seconds(120, timed_out))
        def called(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error as error:
                if path in self.requests:
                    cleanup()
                    done(2, {'error': error.message})
        self.bus.call(PORTAL, PORTAL_PATH, 'org.freedesktop.portal.' + interface, method,
                      GLib.Variant(signature, (*args, options)), None, Gio.DBusCallFlags.NONE, 10000, None, called)

    def close_object(self, path, kind):
        self.bus.call(PORTAL, path, 'org.freedesktop.portal.' + kind, 'Close', None, None,
                      Gio.DBusCallFlags.NONE, 5000, None, None)

    def close(self):
        self.closed = True
        for path, (subscription, timer) in list(self.requests.items()):
            self.bus.signal_unsubscribe(subscription)
            GLib.source_remove(timer)
            self.close_object(path, 'Request')
        self.requests.clear()
        for path in self.sessions:
            self.close_object(path, 'Session')
        self.sessions.clear()


class Shortcuts:
    def __init__(self, portal, toggle, changed):
        self.portal, self.toggle, self.changed = portal, toggle, changed
        self.session = None
        self.state = 'Registering Ctrl+Alt+B…'
        self.subscription = portal.bus.signal_subscribe(PORTAL, 'org.freedesktop.portal.GlobalShortcuts',
            'Activated', PORTAL_PATH, None, Gio.DBusSignalFlags.NONE, self.activated)
        portal.request('GlobalShortcuts', 'CreateSession', (), '(a{sv})', self.created, session=True)

    def created(self, code, results):
        if self.portal.closed:
            return
        if code:
            self.set_state('Global shortcut unavailable — bind blackout --toggle in desktop settings')
            return
        self.session = results['session_handle']
        shortcuts = [('toggle', {'description': GLib.Variant('s', 'Toggle Blackout'),
                                 'preferred_trigger': GLib.Variant('s', 'CTRL+ALT+b')})]
        self.portal.request('GlobalShortcuts', 'BindShortcuts', (self.session, shortcuts, ''),
                            '(oa(sa{sv})sa{sv})', self.bound)

    def bound(self, code, results):
        shortcuts = results.get('shortcuts', [])
        matching = [options for key, options in shortcuts if key == 'toggle']
        self.set_state((matching[0].get('trigger_description') or 'Unassigned — configure in desktop keyboard settings') if not code and matching
                       else 'Global shortcut not granted — bind blackout --toggle in desktop settings')

    def set_state(self, text):
        self.state = text
        self.changed()

    def activated(self, _bus, _sender, _path, _interface, _signal, params):
        session, key, _timestamp, options = params.unpack()
        if session == self.session and key == 'toggle':
            self.toggle()

    def close(self):
        self.portal.bus.signal_unsubscribe(self.subscription)


class KDEShortcut:
    """Use Plasma's native shortcut registry, including Wayland sessions."""
    def __init__(self, bus, toggle, changed):
        self.bus, self.toggle, self.changed = bus, toggle, changed
        self.action = [APP_ID, 'Toggle', 'Blackout', 'Toggle Blackout']
        self.call('doRegister', '(as)', (self.action,))
        # SetPresent=2 activates it; autoload preserves the user's KDE assignment.
        keys = self.call('setShortcut', '(asaiu)', (self.action, [0x04000000 | 0x08000000 | ord('B')], 2)).unpack()[0]
        self.call('setShortcut', '(asaiu)', (self.action, [0x04000000 | 0x08000000 | ord('B')], 8))
        path = self.call('getComponent', '(s)', (APP_ID,)).unpack()[0]
        self.subscription = bus.signal_subscribe('org.kde.kglobalaccel', 'org.kde.kglobalaccel.Component',
            'globalShortcutPressed', path, None, Gio.DBusSignalFlags.NONE, self.activated)
        self.key_subscription = bus.signal_subscribe('org.kde.kglobalaccel', 'org.kde.KGlobalAccel',
            'yourShortcutGotChanged', '/kglobalaccel', None, Gio.DBusSignalFlags.NONE, self.keys_changed)
        self.update_state(keys)

    def call(self, method, signature, args):
        return self.bus.call_sync('org.kde.kglobalaccel', '/kglobalaccel', 'org.kde.KGlobalAccel', method,
            GLib.Variant(signature, args), None, Gio.DBusCallFlags.NONE, 3000, None)

    def update_state(self, keys):
        key = next((key for key in keys if key), 0)
        if key:
            modifiers = Gdk.ModifierType(0)
            for qt, gdk in [(0x02000000, Gdk.ModifierType.SHIFT_MASK), (0x04000000, Gdk.ModifierType.CONTROL_MASK),
                            (0x08000000, Gdk.ModifierType.MOD1_MASK), (0x10000000, Gdk.ModifierType.SUPER_MASK)]:
                if key & qt:
                    modifiers |= gdk
            self.state = Gtk.accelerator_get_label(key & 0x01ffffff, modifiers)
        else:
            self.state = 'Unassigned — set Toggle Blackout in KDE keyboard settings'
        self.changed()

    def keys_changed(self, _bus, _sender, _path, _iface, _signal, params):
        action, keys = params.unpack()
        if action[:2] == self.action[:2]:
            self.update_state(keys)

    def activated(self, _bus, _sender, _path, _iface, _signal, params):
        component, action, _timestamp = params.unpack()
        if component == APP_ID and action == self.action[1]:
            self.toggle()

    def close(self):
        self.bus.signal_unsubscribe(self.subscription)
        self.bus.signal_unsubscribe(self.key_subscription)
        self.bus.call('org.kde.kglobalaccel', '/kglobalaccel', 'org.kde.KGlobalAccel', 'setInactive',
                      GLib.Variant('(as)', (self.action,)), None, Gio.DBusCallFlags.NONE, 3000, None, None)


class X11Shortcut:
    """A private X connection, watched by GLib only when an event arrives."""
    def __init__(self, toggle):
        self.toggle = toggle
        self.x = C.CDLL(ctypes.util.find_library('X11'))
        for name, restype, argtypes in [
            ('XOpenDisplay', C.c_void_p, [C.c_char_p]),
            ('XDefaultRootWindow', C.c_ulong, [C.c_void_p]),
            ('XKeysymToKeycode', C.c_uint, [C.c_void_p, C.c_ulong]),
            ('XGrabKey', C.c_int, [C.c_void_p, C.c_int, C.c_uint, C.c_ulong, C.c_int, C.c_int, C.c_int]),
            ('XUngrabKey', C.c_int, [C.c_void_p, C.c_int, C.c_uint, C.c_ulong]),
            ('XSync', C.c_int, [C.c_void_p, C.c_int]),
            ('XConnectionNumber', C.c_int, [C.c_void_p]),
            ('XPending', C.c_int, [C.c_void_p]),
            ('XNextEvent', C.c_int, [C.c_void_p, C.c_void_p]),
            ('XCloseDisplay', C.c_int, [C.c_void_p]),
            ('XSetErrorHandler', C.c_void_p, [C.c_void_p]),
        ]:
            function = getattr(self.x, name)
            function.restype, function.argtypes = restype, argtypes
        self.display = self.x.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('Cannot connect to X11')
        self.root = self.x.XDefaultRootWindow(self.display)
        self.key = self.x.XKeysymToKeycode(self.display, ord('b'))
        self.failed = False
        def error_handler(_display, _event):
            self.failed = True
            return 0
        callback = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(error_handler)
        previous = self.x.XSetErrorHandler(C.cast(callback, C.c_void_p))
        try:
            # Ignore Caps Lock, Num Lock and Scroll Lock when matching the shortcut.
            for locks in (0, 2, 16, 18, 128, 130, 144, 146):
                self.x.XGrabKey(self.display, self.key, 4 | 8 | locks, self.root, False, 1, 1)
            self.x.XSync(self.display, False)
        finally:
            self.x.XSetErrorHandler(previous)
        if self.failed:
            self.x.XUngrabKey(self.display, self.key, 32768, self.root)
            self.x.XCloseDisplay(self.display)
            self.display = None
            raise RuntimeError('Ctrl+Alt+B is already in use')
        self.state = 'Ctrl+Alt+B'
        self.watch = GLib.io_add_watch(self.x.XConnectionNumber(self.display), GLib.IO_IN, self.ready)

    def ready(self, _fd, condition):
        event = (C.c_long * 24)()  # XEvent is 24 native longs on Xlib platforms.
        while self.x.XPending(self.display):
            self.x.XNextEvent(self.display, C.byref(event))
            if C.cast(event, C.POINTER(C.c_int))[0] == 2:  # KeyPress
                self.toggle()
        return GLib.SOURCE_CONTINUE

    def close(self):
        if self.display:
            GLib.source_remove(self.watch)
            self.x.XUngrabKey(self.display, self.key, 32768, self.root)
            self.x.XCloseDisplay(self.display)
            self.display = None


SNI_XML = '''<node><interface name="org.kde.StatusNotifierItem">
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
<property name="Category" type="s" access="read"/>
<property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/>
<property name="Status" type="s" access="read"/>
<property name="WindowId" type="i" access="read"/>
<property name="IconName" type="s" access="read"/>
<property name="IconPixmap" type="a(iiay)" access="read"/>
<property name="OverlayIconName" type="s" access="read"/>
<property name="AttentionIconName" type="s" access="read"/>
<property name="AttentionMovieName" type="s" access="read"/>
<property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<property name="ItemIsMenu" type="b" access="read"/>
<property name="Menu" type="o" access="read"/>
<signal name="NewStatus"><arg type="s"/></signal>
<signal name="NewIcon"/><signal name="NewToolTip"/>
</interface></node>'''


class Tray:
    """StatusNotifierItem with native dbusmenu and distinct left/right click actions."""
    def __init__(self, bus, toggle, menu):
        gi.require_version('Dbusmenu', '0.4')
        gi.require_version('DbusmenuGtk3', '0.4')
        from gi.repository import Dbusmenu, DbusmenuGtk3
        self.bus, self.toggle, self.menu = bus, toggle, menu
        self.server = Dbusmenu.Server.new('/BlackoutMenu')
        self.parse_menu = DbusmenuGtk3.gtk_parse_menu_structure
        self.set_menu(menu)
        self.registration = bus.register_object('/StatusNotifierItem',
            Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0], self.method, self.property, None)
        self.watch = Gio.bus_watch_name_on_connection(bus, 'org.kde.StatusNotifierWatcher',
            Gio.BusNameWatcherFlags.NONE, self.appeared, None)
        self.state = 'StatusNotifierItem'

    def set_menu(self, menu):
        self.menu = menu
        self.server.set_root(self.parse_menu(menu))

    def appeared(self, *_):
        self.bus.call('org.kde.StatusNotifierWatcher', '/StatusNotifierWatcher',
                      'org.kde.StatusNotifierWatcher', 'RegisterStatusNotifierItem',
                      GLib.Variant('(s)', ('/StatusNotifierItem',)), None,
                      Gio.DBusCallFlags.NONE, 5000, None, None)

    def method(self, _bus, _sender, _path, _iface, method, params, invocation):
        if method in ('Activate', 'SecondaryActivate'):
            self.toggle()
        elif method == 'ContextMenu':
            self.menu.popup_at_pointer(None)
        invocation.return_value(None)

    def property(self, _bus, _sender, _path, _iface, name):
        values = {'Category': ('s', 'ApplicationStatus'), 'Id': ('s', APP_ID),
                  'Title': ('s', 'Blackout'), 'Status': ('s', 'Active'), 'WindowId': ('i', 0),
                  'IconName': ('s', 'weather-clear-night'), 'IconPixmap': ('a(iiay)', []),
                  'OverlayIconName': ('s', ''), 'AttentionIconName': ('s', ''),
                  'AttentionMovieName': ('s', ''), 'ItemIsMenu': ('b', False),
                  'Menu': ('o', '/BlackoutMenu'),
                  'ToolTip': ('(sa(iiay)ss)', ('weather-clear-night', [], 'Blackout', 'Click to toggle; right-click for options'))}
        signature, value = values[name]
        return GLib.Variant(signature, value)

    def close(self):
        Gio.bus_unwatch_name(self.watch)
        self.bus.unregister_object(self.registration)
        self.server = None


class Desktop:
    def __init__(self, app):
        self.app = app
        self.bus = app.get_dbus_connection()
        self.portal = Portal(self.bus)
        self.inhibition = None
        self.inhibit_generation = 0
        self.subscriptions = []
        self.lock_session = None
        self.tray = None
        self.shortcut = None
        for service, path, interface in [
            ('org.freedesktop.ScreenSaver', '/ScreenSaver', 'org.freedesktop.ScreenSaver'),
            ('org.freedesktop.ScreenSaver', '/org/freedesktop/ScreenSaver', 'org.freedesktop.ScreenSaver'),
            ('org.gnome.ScreenSaver', '/org/gnome/ScreenSaver', 'org.gnome.ScreenSaver'),
        ]:
            self.subscriptions.append(self.bus.signal_subscribe(service, interface, 'ActiveChanged', path,
                None, Gio.DBusSignalFlags.NONE, self.lock_changed))
        self.portal.request('Inhibit', 'CreateMonitor', ('',), '(sa{sv})', self.monitor_created, session=True)
        self.subscriptions.append(self.bus.signal_subscribe(PORTAL, 'org.freedesktop.portal.Inhibit',
            'StateChanged', PORTAL_PATH, None, Gio.DBusSignalFlags.NONE, self.portal_state))
        try:
            self.tray = Tray(self.bus, app.toggle, app.menu)
        except (ValueError, ImportError) as error:
            app.integration_error = f'Tray unavailable: {error}. Install the GTK dbusmenu bindings.'
        if 'KDE' in os.environ.get('XDG_CURRENT_DESKTOP', '').split(':'):
            try:
                self.shortcut = KDEShortcut(self.bus, app.toggle, app.integrations_changed)
            except GLib.Error as error:
                app.integration_error = 'KDE shortcut unavailable: ' + error.message
        if (self.shortcut is None and app.display.__gtype__.name == 'GdkX11Display'
                and os.environ.get('XDG_SESSION_TYPE') != 'wayland'):
            try:
                self.shortcut = X11Shortcut(app.toggle)
            except (OSError, RuntimeError) as error:
                app.integration_error = str(error)
        if self.shortcut is None:
            self.shortcut = Shortcuts(self.portal, app.toggle, app.integrations_changed)

    def monitor_created(self, code, result):
        if not code:
            self.lock_session = result['session_handle']

    def lock_changed(self, _bus, _sender, _path, _interface, _signal, params):
        if params.unpack()[0] and self.app.settings['lock']:
            self.app.stop()

    def portal_state(self, _bus, _sender, _path, _interface, _signal, params):
        session, state = params.unpack()
        if session != self.lock_session:
            return
        if state.get('screensaver-active') and self.app.settings['lock']:
            self.app.stop()
        if state.get('session-state') == 2:
            self.bus.call(PORTAL, PORTAL_PATH, 'org.freedesktop.portal.Inhibit', 'QueryEndResponse',
                          GLib.Variant('(o)', (session,)), None, Gio.DBusCallFlags.NONE, 5000, None, None)

    def keep_awake(self, enabled):
        self.inhibit_generation += 1
        generation = self.inhibit_generation
        if self.inhibition:
            self.portal.close_object(self.inhibition, 'Request')
            self.inhibition = None
        if not enabled:
            return
        def finished(bus, result):
            try:
                path = bus.call_finish(result).unpack()[0]
                if generation != self.inhibit_generation or self.portal.closed:
                    self.portal.close_object(path, 'Request')
                else:
                    self.inhibition = path
                    self.app.integrations_changed()
            except GLib.Error as error:
                self.app.integration_error = 'Keep-awake unavailable: ' + error.message
                self.app.integrations_changed()
        self.bus.call(PORTAL, PORTAL_PATH, 'org.freedesktop.portal.Inhibit', 'Inhibit',
            GLib.Variant('(sua{sv})', ('', 12, {'reason': GLib.Variant('s', 'Blackout is active')})),
            None, Gio.DBusCallFlags.NONE, 10000, None, finished)

    def close(self):
        self.keep_awake(False)
        if self.shortcut:
            self.shortcut.close()
        if self.tray:
            self.tray.close()
        for subscription in self.subscriptions:
            self.bus.signal_unsubscribe(subscription)
        self.portal.close()
