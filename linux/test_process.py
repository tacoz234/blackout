"""Exercise the installed app and real single-instance command routing."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from gi.repository import Gio, GLib

ROOT = Path(__file__).resolve().parent.parent


class ResidentProcess(unittest.TestCase):
    def test_background_single_instance_commands_and_quit(self):
        with tempfile.TemporaryDirectory(prefix='blackout process ') as directory:
            home = Path(directory)
            config = home / 'bus.conf'
            config.write_text('''<busconfig><type>session</type><listen>unix:tmpdir=/tmp</listen>
<auth>EXTERNAL</auth><policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/></policy></busconfig>''')
            daemon = subprocess.Popen(['dbus-daemon', '--nofork', '--print-address=1', '--config-file=' + str(config)], stdout=subprocess.PIPE, text=True)
            address = daemon.stdout.readline().strip()
            env = dict(os.environ, HOME=directory, XDG_CONFIG_HOME=str(home / 'config'),
                       XDG_DATA_HOME=str(home / 'data'), DBUS_SESSION_BUS_ADDRESS=address,
                       XDG_CURRENT_DESKTOP='', XDG_SESSION_TYPE='x11', GDK_BACKEND='x11')
            subprocess.run([ROOT / 'linux/build.sh', '--install', '--autostart'], env=env, check=True, capture_output=True)
            command = home / '.local/bin/blackout'
            with (home / 'app.log').open('w+') as log:
                process = subprocess.Popen([command, '--background'], env=env, stdout=log, stderr=log)
                try:
                    flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
                    bus = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        owned = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'NameHasOwner',
                            GLib.Variant('(s)', ('io.github.tacoz234.Blackout',)), None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
                        if owned:
                            break
                        self.assertIsNone(process.poll())
                        time.sleep(0.02)
                    self.assertTrue(owned)
                    def run(option):
                        return subprocess.check_output([command, option], env=env, text=True, timeout=5)
                    def status():
                        return json.loads(run('--status'))
                    self.assertFalse(status()['active'])
                    self.assertTrue(status()['background'])
                    self.assertTrue(status()['autostart'])
                    for _ in range(2):
                        run('--background')
                        self.assertFalse(status()['active'])
                    run('--toggle')
                    self.assertTrue(status()['active'])
                    run('--toggle')
                    self.assertFalse(status()['active'])
                    run('--preferences')
                    self.assertFalse(status()['background'])
                    run('--stop')
                    pid = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'GetConnectionUnixProcessID',
                        GLib.Variant('(s)', ('io.github.tacoz234.Blackout',)), None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
                    self.assertEqual(pid, process.pid)
                    run('--quit')
                    self.assertEqual(process.wait(timeout=5), 0)
                    bus.close_sync(None)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=5)
                    daemon.terminate()
                    daemon.wait(timeout=5)
                    daemon.stdout.close()
                    log.seek(0)
                    self.assertNotIn('Traceback', log.read())


if __name__ == '__main__':
    unittest.main(verbosity=2)
