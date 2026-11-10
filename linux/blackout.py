#!/usr/bin/env python3
"""Blackout for Linux: persistent tray app, event-driven GTK 3 overlays."""
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, Gtk

import desktop
import updater

CONFIG = desktop.CONFIG_HOME / 'blackout/settings.json'
SCRIPT = Path(__file__).resolve()
# Optional per-user native libraries are useful on immutable Linux desktops.
_runtime = desktop.DATA_HOME / 'blackout-runtime/lib'
if _runtime.is_dir():
    gi.Repository.get_default().prepend_search_path(str(_runtime / 'girepository-1.0'))
    gi.Repository.get_default().prepend_library_path(str(_runtime))
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
except (ValueError, ImportError):
    GtkLayerShell = None
USAGE = 'Usage: blackout [--toggle|--stop|--background|--preferences|--status|--quit]'


def format_clock(now, use_24_hour):
    if use_24_hour:
        return f'{now.tm_hour:02d}:{now.tm_min:02d}'
    return f'{now.tm_hour % 12 or 12}:{now.tm_min:02d} {"AM" if now.tm_hour < 12 else "PM"}'


class Cover(Gtk.ApplicationWindow):
    def __init__(self, app, index, partial):
        super().__init__(application=app, title='Blackout')
        self.owner = app
        self.timers = {}
        self.anchor = None
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_focus_on_map(not partial)
        self.get_style_context().add_class('blackout')
        self.overlay = Gtk.Label()
        self.overlay.get_style_context().add_class('hint')
        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                     transition_duration=1000, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.revealer.add(self.overlay)
        self.add(self.revealer)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect('key-press-event', self.key_pressed)
        self.connect('motion-notify-event', self.motion)
        self.connect('button-press-event', self.clicked)
        self.connect('delete-event', self.end_event)
        self.connect('realize', self.hide_cursor)
        self.layer_shell = bool(partial and GtkLayerShell and GtkLayerShell.is_supported())
        if self.layer_shell:
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_namespace(self, 'blackout')
            GtkLayerShell.set_monitor(self, app.display.get_monitor(index))
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_exclusive_zone(self, -1)
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                         GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(self, edge, True)
            GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        else:
            self.fullscreen_on_monitor(self.get_screen(), index)

    def schedule(self, key, milliseconds, callback):
        self.cancel(key)
        def fire():
            self.timers.pop(key, None)
            callback()
            return GLib.SOURCE_REMOVE
        self.timers[key] = GLib.timeout_add(milliseconds, fire)

    def cancel(self, key):
        source = self.timers.pop(key, None)
        if source:
            GLib.source_remove(source)

    def flash(self, text, clock=False):
        style = self.overlay.get_style_context()
        style.remove_class('clock' if not clock else 'hint')
        style.add_class('clock' if clock else 'hint')
        self.overlay.set_text(text)
        # Restart the hold without fading in on each pointer event.
        self.revealer.set_transition_duration(0)
        self.revealer.set_reveal_child(True)
        self.revealer.set_transition_duration(1000)
        self.schedule('text', 2000 if clock else 2500, lambda: self.revealer.set_reveal_child(False))

    def hide_cursor(self, *_):
        self.get_window().set_cursor(Gdk.Cursor.new_for_display(self.owner.display, Gdk.CursorType.BLANK_CURSOR))

    def key_pressed(self, _window, event):
        if (event.keyval == Gdk.KEY_Escape
                or (event.keyval == Gdk.KEY_F4 and getattr(event, 'state', 0) & Gdk.ModifierType.MOD1_MASK)
                or self.owner.settings['input']):
            self.owner.stop()
            return True
        return False

    def clicked(self, _window, _event):
        if self.owner.settings['input']:
            self.owner.stop()
        else:
            if self.layer_shell:
                GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
            else:
                self.present()
        return True

    def motion(self, _window, event):
        position = (event.x_root, event.y_root)
        previous = self.anchor
        self.anchor = position
        if time.monotonic() - self.owner.started_at < 1 or previous is None:
            return False
        if abs(position[0] - previous[0]) + abs(position[1] - previous[1]) < 6:
            self.anchor = previous
            return False
        if self.owner.settings['input']:
            self.owner.stop()
        elif self.owner.settings['clock']:
            self.flash(format_clock(time.localtime(), self.owner.settings['clock24']), clock=True)
        return False

    def end_event(self, *_):
        self.owner.stop()
        return True

    def dispose(self):
        for key in list(self.timers):
            self.cancel(key)
        self.destroy()


class Blackout(Gtk.Application):
    def __init__(self, integrations=True, application_id=desktop.APP_ID):
        super().__init__(application_id=application_id, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.covers = []
        self.controls = None
        self.desktop = None
        self.use_integrations = integrations
        self.integration_error = ''
        self.auto_exit = None
        self.update_timer = None
        self.update_busy = False
        self.update = None
        self.update_status = ''
        self.restart = False
        self.started_at = 0
        self.settings = {'excluded': [], 'minutes': 0, 'awake': True, 'input': False,
                         'clock': False, 'clock24': False, 'lock': True, 'updates': True}
        try:
            saved = json.loads(CONFIG.read_text())
            if isinstance(saved, dict):
                for key, value in self.settings.items():
                    if type(saved.get(key)) is type(value):
                        self.settings[key] = saved[key]
        except (OSError, ValueError):
            pass
        self.settings['excluded'] = [v for v in self.settings['excluded'] if isinstance(v, str)]
        if self.settings['minutes'] not in (0, 15, 30, 60, 120, 240):
            self.settings['minutes'] = 0

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        css = Gtk.CssProvider()
        css.load_from_data(b'.blackout { background: #000; color: #999; } '
                           b'.blackout label { background: transparent; color: #999; text-shadow: none; } '
                           b'.blackout .hint { font: 28px Sans; } .blackout .clock { font: 160px Sans; font-weight: 300; }')
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.display = Gdk.Display.get_default()
        self.display.connect('monitor-added', self.monitors_changed)
        self.display.connect('monitor-removed', self.monitors_changed)
        self.build_menu()
        if self.use_integrations:
            self.desktop = desktop.Desktop(self)
            self.schedule_updates(initial=True)
        self.signal_sources = [GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.quit_app)
                               for sig in (signal.SIGTERM, signal.SIGINT)]

    def do_activate(self):
        self.start()

    def do_command_line(self, command):
        args = command.get_arguments()[1:]
        if len(args) > 1 or any(a not in ('--toggle', '--stop', '--background', '--preferences', '--status', '--quit') for a in args):
            command.printerr(USAGE + '\n')
            return 2
        option = args[0] if args else ''
        if option == '--quit':
            self.quit_app()
        elif option == '--stop':
            self.stop()
        elif option == '--background':
            pass
        elif option == '--preferences':
            self.preferences()
        elif option == '--status':
            command.print_literal(json.dumps(self.status()) + '\n')
        elif option == '--toggle':
            self.toggle()
        else:
            self.start()
        return 0

    def status(self):
        return {'version': updater.current_version(), 'active': bool(self.covers), 'windows': len(self.covers),
                'monitors': self.display.get_n_monitors(), 'layer_shell': bool(GtkLayerShell and GtkLayerShell.is_supported()), 'background': not self.controls or not self.controls.get_visible(),
                'autostart': desktop.AUTOSTART.exists(), 'shortcut': self.desktop.shortcut.state if self.desktop else None,
                'tray': self.desktop.tray.state if self.desktop and self.desktop.tray else None,
                'inhibited': bool(self.desktop and self.desktop.inhibition), 'error': self.integration_error,
                'update': self.update_status}

    def save(self):
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            temp = CONFIG.with_suffix('.tmp')
            temp.write_text(json.dumps(self.settings, indent=2) + '\n')
            temp.replace(CONFIG)
        except OSError as error:
            self.notify('Could not save preferences', str(error))

    def monitors(self):
        return [self.display.get_monitor(i) for i in range(self.display.get_n_monitors())]

    def monitor_key(self, monitor, index):
        connector = Gdk.Screen.get_default().get_monitor_plug_name(index)
        return connector or f'{index}:{monitor.get_manufacturer() or ""}:{monitor.get_model() or "Display"}'

    def monitors_changed(self, *_):
        active = bool(self.covers)
        self.stop()
        self.build_menu()
        if self.desktop and self.desktop.tray:
            self.desktop.tray.set_menu(self.menu)
        if self.controls:
            self.controls.destroy()
            self.controls = None
        if active:
            self.start()

    def toggle(self):
        self.stop() if self.covers else self.start()

    def start(self):
        if self.covers:
            return
        monitors = self.monitors()
        chosen = [(i, m) for i, m in enumerate(monitors) if self.monitor_key(m, i) not in self.settings['excluded']]
        if not chosen:
            chosen = list(enumerate(monitors))
        if not chosen:
            return
        if self.controls:
            self.controls.hide()
        self.started_at = time.monotonic()
        partial = len(chosen) < len(monitors)
        for index, _monitor in chosen:
            window = Cover(self, index, partial)
            self.covers.append(window)
            window.show_all()
        self.covers[0].flash('Press Ctrl+Alt+B to exit' if partial else 'Press Escape to exit')
        self.apply_awake()
        self.reset_timer()
        self.refresh_menu()

    def reset_timer(self):
        if self.auto_exit:
            GLib.source_remove(self.auto_exit)
            self.auto_exit = None
        if self.covers and self.settings['minutes']:
            self.auto_exit = GLib.timeout_add_seconds(self.settings['minutes'] * 60, self.auto_stop)

    def auto_stop(self):
        self.auto_exit = None
        self.stop()
        return GLib.SOURCE_REMOVE

    def apply_awake(self):
        if self.desktop:
            self.desktop.keep_awake(bool(self.covers) and self.settings['awake'])

    def stop(self):
        for window in self.covers:
            window.dispose()
        self.covers.clear()
        self.reset_timer()
        self.apply_awake()
        self.refresh_menu()

    def integrations_changed(self):
        if self.controls and hasattr(self, 'integration_label'):
            self.integration_label.set_text(self.integration_text())

    def integration_text(self):
        shortcut = self.desktop.shortcut.state if self.desktop and self.desktop.shortcut else 'Not registered'
        return f'Global shortcut: {shortcut}\n{self.integration_error}'.strip()

    def notify(self, title, message):
        if self.use_integrations:
            notification = Gio.Notification.new(title)
            notification.set_body(message)
            self.send_notification(None, notification)
        else:
            print(f'{title}: {message}', file=sys.stderr)

    def quit_app(self):
        self.quit()
        return GLib.SOURCE_REMOVE

    def do_shutdown(self):
        self.stop()
        if self.update_timer:
            GLib.source_remove(self.update_timer)
            self.update_timer = None
        for source in self.signal_sources:
            if GLib.MainContext.default().find_source_by_id(source):
                GLib.source_remove(source)
        if self.desktop:
            self.desktop.close()
        Gtk.Application.do_shutdown(self)

    def set_boolean(self, check, key):
        value = check.get_active()
        if self.settings[key] == value:
            return
        self.settings[key] = value
        self.save()
        if key == 'awake':
            self.apply_awake()
        elif key == 'updates':
            self.schedule_updates(initial=True)
        self.refresh_menu()

    def select_monitor(self, check, key):
        excluded = self.settings['excluded']
        self.settings['excluded'] = [item for item in excluded if item != key]
        if not check.get_active():
            self.settings['excluded'].append(key)
        self.save()
        if self.covers:
            self.stop()
            self.start()
        self.refresh_menu()

    def set_minutes(self, _item, minutes):
        self.settings['minutes'] = minutes
        self.save()
        self.reset_timer()
        self.refresh_menu()

    def set_login(self, item):
        try:
            desktop.set_autostart(item.get_active(), SCRIPT)
        except OSError as error:
            self.notify('Could not change login startup', str(error))
        self.refresh_menu()

    def build_menu(self):
        self.menu = Gtk.Menu()
        self.boolean_items = {}
        self.monitor_items = {}
        self.timer_items = {}
        def add(label, callback=None, parent=None):
            item = Gtk.MenuItem(label=label)
            if callback:
                item.connect('activate', lambda *_: callback())
            (parent if parent is not None else self.menu).append(item)
            return item
        add('Blackout ' + updater.current_version()).set_sensitive(False)
        self.update_item = add('Update Blackout', self.install_update)
        self.toggle_item = add('Black out now', self.toggle)
        displays = Gtk.Menu()
        add('Displays').set_submenu(displays)
        for index, monitor in enumerate(self.monitors()):
            key = self.monitor_key(monitor, index)
            item = Gtk.CheckMenuItem(label=f'{index + 1}: {monitor.get_model() or key}')
            item.set_active(key not in self.settings['excluded'])
            item.connect('toggled', self.select_monitor, key)
            displays.append(item)
            self.monitor_items[key] = item
        timers = Gtk.Menu()
        add('Auto-exit').set_submenu(timers)
        for minutes in (0, 15, 30, 60, 120, 240):
            label = 'Never' if minutes == 0 else f'{minutes} minutes'
            item = Gtk.CheckMenuItem(label=label)
            item.set_draw_as_radio(True)
            item.connect('activate', self.set_minutes, minutes)
            timers.append(item)
            self.timer_items[minutes] = item
        for key, label in [('awake', 'Keep display awake'), ('input', 'Exit on mouse move or any key'),
                           ('clock', 'Show clock on mouse move'), ('clock24', 'Use 24-hour clock'),
                           ('lock', 'End blackout when computer locks'),
                           ('updates', 'Check for updates automatically')]:
            item = Gtk.CheckMenuItem(label=label)
            item.set_active(self.settings[key])
            item.connect('toggled', self.set_boolean, key)
            self.menu.append(item)
            self.boolean_items[key] = item
        self.login_item = Gtk.CheckMenuItem(label='Start at login')
        self.login_item.set_active(desktop.AUTOSTART.exists())
        self.login_item.connect('toggled', self.set_login)
        self.menu.append(self.login_item)
        self.check_item = add('Check for updates now', lambda: self.check_updates(True))
        add('Options', self.preferences)
        add('Quit Blackout', self.quit_app)
        self.menu.show_all()
        self.refresh_menu()

    def refresh_menu(self):
        self.toggle_item.set_label('End blackout' if self.covers else 'Black out now')
        self.update_item.set_visible(self.update is not None)
        self.update_item.set_label('Downloading update…' if self.update_busy else
                                   ('Update to Blackout ' + self.update['version'] if self.update else 'Update Blackout'))
        self.update_item.set_sensitive(not self.update_busy)
        self.check_item.set_sensitive(not self.update_busy)
        # Setting active emits activate for check menu items; block callbacks while syncing.
        for key, item in self.monitor_items.items():
            item.handler_block_by_func(self.select_monitor)
            item.set_active(key not in self.settings['excluded'])
            item.handler_unblock_by_func(self.select_monitor)
        for minutes, item in self.timer_items.items():
            item.handler_block_by_func(self.set_minutes)
            item.set_active(self.settings['minutes'] == minutes)
            item.handler_unblock_by_func(self.set_minutes)
        for key, item in self.boolean_items.items():
            item.handler_block_by_func(self.set_boolean)
            item.set_active(self.settings[key])
            item.handler_unblock_by_func(self.set_boolean)
        self.login_item.handler_block_by_func(self.set_login)
        self.login_item.set_active(desktop.AUTOSTART.exists())
        self.login_item.handler_unblock_by_func(self.set_login)

    def preferences(self):
        if self.controls:
            self.controls.destroy()
        window = Gtk.ApplicationWindow(application=self, title='Blackout options')
        self.controls = window
        window.set_default_size(440, 400)
        window.connect('delete-event', self.close_controls)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=20)
        window.add(box)
        box.pack_start(Gtk.Label(label='Blackout keeps running when this window closes.', xalign=0), False, False, 0)
        for index, monitor in enumerate(self.monitors()):
            key = self.monitor_key(monitor, index)
            check = Gtk.CheckButton(label=f'Display {index + 1}: {monitor.get_model() or key}')
            check.set_active(key not in self.settings['excluded'])
            check.connect('toggled', self.select_monitor, key)
            box.pack_start(check, False, False, 0)
        for key, item in self.boolean_items.items():
            check = Gtk.CheckButton(label=item.get_label())
            check.set_active(self.settings[key])
            check.connect('toggled', self.set_boolean, key)
            box.pack_start(check, False, False, 0)
        login = Gtk.CheckButton(label='Start at login')
        login.set_active(desktop.AUTOSTART.exists())
        login.connect('toggled', self.set_login)
        box.pack_start(login, False, False, 0)
        timer = Gtk.ComboBoxText()
        for minutes in self.timer_items:
            timer.append(str(minutes), 'Auto-exit: never' if not minutes else f'Auto-exit: {minutes} minutes')
        timer.set_active_id(str(self.settings['minutes']))
        timer.connect('changed', lambda combo: self.set_minutes(None, int(combo.get_active_id())))
        box.pack_start(timer, False, False, 0)
        self.integration_label = Gtk.Label(label=self.integration_text(), xalign=0, wrap=True)
        box.pack_start(self.integration_label, False, False, 0)
        if self.update:
            button = Gtk.Button(label='Update to Blackout ' + self.update['version'])
            button.connect('clicked', lambda *_: self.install_update())
            box.pack_start(button, False, False, 0)
        row = Gtk.Box(spacing=8)
        for label, callback in [('Black out now', self.start), ('Check for updates', lambda: self.check_updates(True)), ('Quit', self.quit_app)]:
            button = Gtk.Button(label=label)
            button.connect('clicked', lambda _, cb=callback: cb())
            row.pack_start(button, True, True, 0)
        box.pack_start(row, False, False, 0)
        window.show_all()
        window.present()

    def close_controls(self, window, _event):
        window.hide()
        return True

    def schedule_updates(self, initial=False):
        if self.update_timer:
            GLib.source_remove(self.update_timer)
            self.update_timer = None
        if self.use_integrations and self.settings['updates'] and '-' not in updater.current_version():
            def tick():
                self.update_timer = None
                self.check_updates(False)
                self.schedule_updates()
                return GLib.SOURCE_REMOVE
            self.update_timer = GLib.timeout_add_seconds(10 if initial else 86400, tick)

    def check_updates(self, manual=False):
        if self.update_busy:
            return
        self.update_busy = True
        self.refresh_menu()
        def worker():
            try:
                release, error = updater.find_newer(), None
            except Exception as caught:
                release, error = None, str(caught)
            def finish():
                self.update_busy = False
                self.update = release
                self.update_status = error or ('Update available: ' + release['version'] if release else 'Up to date')
                self.refresh_menu()
                if manual or release:
                    self.notify('Blackout updates', self.update_status)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(finish)
        threading.Thread(target=worker, daemon=True).start()

    def install_update(self):
        if not self.update or self.update_busy:
            return
        if SCRIPT.parent != desktop.DATA_HOME / 'blackout':
            self.notify('Install Blackout first', 'Run linux/build.sh --install before updating this source checkout.')
            return
        self.update_busy = True
        self.refresh_menu()
        def worker():
            try:
                updater.install(self.update, SCRIPT.parent)
                error = None
            except Exception as caught:
                error = str(caught)
            def finish():
                self.update_busy = False
                if error:
                    self.notify('Could not install update', error)
                    self.refresh_menu()
                else:
                    self.restart = True
                    self.quit_app()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(finish)
        threading.Thread(target=worker, daemon=True).start()


if __name__ == '__main__':
    if '--help' in sys.argv:
        print(USAGE)
        sys.exit(0)
    if len(sys.argv) > 2 or any(arg not in ('--toggle', '--stop', '--background', '--preferences', '--status', '--quit') for arg in sys.argv[1:]):
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    # Identify the installed app before GTK or our integrations use a portal.
    try:
        Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
            desktop.PORTAL, desktop.PORTAL_PATH, 'org.freedesktop.host.portal.Registry', 'Register',
            GLib.Variant('(sa{sv})', (desktop.APP_ID, {})), None, Gio.DBusCallFlags.NONE, 3000, None)
    except GLib.Error:
        pass  # Older portals infer the identity from the launch environment.
    # GNOME and other compositors without layer-shell use Xwayland's nonactivating windows.
    if ('GDK_BACKEND' not in os.environ and os.environ.get('WAYLAND_DISPLAY') and os.environ.get('DISPLAY')):
        Gtk.init([])
        if not (GtkLayerShell and GtkLayerShell.is_supported()):
            os.environ['GDK_BACKEND'] = 'x11'
            os.execv(sys.executable, [sys.executable, str(SCRIPT), *sys.argv[1:]])
    GLib.set_prgname(desktop.APP_ID)
    GLib.set_application_name('Blackout')
    app = Blackout()
    result = app.run(sys.argv)
    if app.restart:
        os.execv(sys.executable, [sys.executable, str(SCRIPT), '--background'])
    sys.exit(result)
