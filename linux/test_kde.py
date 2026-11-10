"""Opt-in live KDE test: briefly blacks out displays; requires installed running app.

Run: python3 linux/test_kde.py
Uses KWin's window state to verify fullscreen geometry and partial-display focus.
Does not lock the real session or modify its shortcut assignments.
"""
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gio, GLib, Gtk

APP = 'io.github.tacoz234.Blackout'
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
launcher = Path.home() / '.local/bin/blackout'


def call(destination, path, interface, method, signature=None, args=()):
    return bus.call_sync(destination, path, interface, method,
                         GLib.Variant(signature, args) if signature else None,
                         None, Gio.DBusCallFlags.NONE, 5000, None)


def status():
    return json.loads(subprocess.check_output([launcher, '--status'], text=True))


def drain(seconds):
    deadline = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)


def menu():
    _, layout = call(APP, '/BlackoutMenu', 'com.canonical.dbusmenu', 'GetLayout', '(iias)', (0, -1, [])).unpack()
    def flatten(node):
        identity, props, children = node
        return [(identity, props)] + [pair for child in children for pair in flatten(child)]
    return flatten(layout)


def click(label):
    identity = next(identity for identity, props in menu() if props.get('label') == label)
    call(APP, '/BlackoutMenu', 'com.canonical.dbusmenu', 'Event', '(isvu)',
         (identity, 'clicked', GLib.Variant('i', 0), 0))
    drain(0.15)


reports = []
node = Gio.DBusNodeInfo.new_for_xml('<node><interface name="io.github.Blackout.Test">'
    '<method name="Record"><arg type="s" direction="in"/></method></interface></node>')

def record(_bus, _sender, _path, _interface, _method, args, invocation):
    reports.append(json.loads(args.unpack()[0]))
    invocation.return_value(None)

registration = bus.register_object('/Results', node.interfaces[0], record, None, None)


def window_state():
    token = 'blackout_test_' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'state.js'
        path.write_text('''function info(w) {
            if (!w) return null;
            const g = w.frameGeometry;
            return {id: w.internalId.toString(), title: w.caption, resourceClass: w.resourceClass.toString(),
                    pid: w.pid, fullScreen: w.fullScreen, x:g.x, y:g.y, width:g.width, height:g.height};
        }
        callDBus(''' + json.dumps(bus.get_unique_name()) + ''', '/Results', 'io.github.Blackout.Test', 'Record',
            JSON.stringify({active: info(workspace.activeWindow), windows: workspace.windowList().map(info)}));''')
        identifier = call('org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting', 'loadScript', '(ss)', (str(path), token)).unpack()[0]
        try:
            call('org.kde.KWin', f'/Scripting/Script{identifier}', 'org.kde.kwin.Script', 'run')
            deadline = time.monotonic() + 3
            while not reports and time.monotonic() < deadline:
                drain(0.02)
            assert reports, 'KWin did not report its window state'
            return reports.pop()
        finally:
            call('org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting', 'unloadScript', '(s)', (token,))


result = {}
original = menu()
displays = [(identity, props) for identity, props in original if props.get('label', '').startswith(('1:', '2:', '3:', '4:'))]
assert len(displays) >= 2, 'This test needs two displays'
try:
    current = status()
    assert current['background'] and not current['active'] and current['autostart']
    assert current['tray'] == 'StatusNotifierItem' and current['shortcut'] == 'Ctrl+Alt+B', current
    for _, props in displays:
        if props.get('toggle-state') != 1:
            click(props['label'])
    # A real fullscreen client on the uncovered primary output is the focus probe.
    probe = Gtk.Window(title='Blackout fullscreen focus probe')
    probe.add(Gtk.Label(label='Blackout focus test'))
    probe.fullscreen_on_monitor(probe.get_screen(), 1)
    probe.show_all()
    probe.present()
    drain(0.5)
    before = window_state()
    # Native tray Activate must toggle without launching another copy.
    call(APP, '/StatusNotifierItem', 'org.kde.StatusNotifierItem', 'Activate', '(ii)', (0, 0))
    drain(0.6)
    active = status()
    assert active['active'] and active['windows'] == active['monitors'], active
    assert active['inhibited'], active
    full = window_state()
    app_pid = call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetConnectionUnixProcessID', '(s)', (APP,)).unpack()[0]
    covers = [w for w in full['windows'] if w['pid'] == app_pid]
    assert len(covers) == active['monitors'], full
    assert all(w['fullScreen'] or active['layer_shell'] for w in covers), covers
    result['fullscreen_windows'] = covers
    subprocess.run([launcher, '--stop'], check=True)
    drain(0.3)
    assert not status()['inhibited']
    restored = window_state()
    assert restored['active'] and restored['active']['id'] == before['active']['id'], restored
    result['full_blackout_restores_focus'] = 'passed'
    # Exclude one monitor through the actual exported tray menu.
    click(displays[-1][1]['label'])
    probe.present()
    drain(0.3)
    before_partial = window_state()
    assert before_partial['active'] and before_partial['active']['title'] == 'Blackout fullscreen focus probe', before_partial
    call('org.kde.kglobalaccel', '/component/io_github_tacoz234_Blackout',
         'org.kde.kglobalaccel.Component', 'invokeShortcut', '(s)', ('Toggle',))
    drain(0.5)
    partial = window_state()
    assert status()['windows'] == active['monitors'] - 1
    result['partial_before'] = before_partial['active']
    result['partial_after'] = partial['active']
    assert partial['active']['id'] == before_partial['active']['id'], result
    call('org.kde.kglobalaccel', '/component/io_github_tacoz234_Blackout',
         'org.kde.kglobalaccel.Component', 'invokeShortcut', '(s)', ('Toggle',))
    drain(0.2)
    assert not status()['active']
    click(displays[-1][1]['label'])
    call(APP, '/StatusNotifierItem', 'org.kde.StatusNotifierItem', 'Activate', '(ii)', (0, 0))
    drain(0.4)
    call('org.kde.kglobalaccel', '/component/kwin', 'org.kde.kglobalaccel.Component', 'invokeShortcut', '(s)', ('Window Close',))
    drain(0.3)
    assert not status()['active'], 'Compositor close (Alt+F4) must end every blackout window'
    result['compositor_close'] = 'passed'
    result['tray_shortcut_and_inhibition'] = 'passed'
finally:
    subprocess.run([launcher, '--stop'], check=True)
    if 'probe' in globals():
        probe.destroy()
        drain(0.1)
    for _, props in displays:
        now = next(p for _, p in menu() if p.get('label') == props['label'])
        if now.get('toggle-state') != props.get('toggle-state'):
            click(props['label'])
    bus.unregister_object(registration)

Path('/tmp/blackout-kde-results.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
print('Live KDE tests passed')
