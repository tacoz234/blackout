"""Install and release artifact regression tests without touching the real home."""
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

import updater

ROOT = Path(__file__).resolve().parent.parent


class Packaging(unittest.TestCase):
    def test_install_autostart_wrapper_and_desktop_paths(self):
        with tempfile.TemporaryDirectory(prefix='blackout $`% spaces ') as directory:
            home = Path(directory)
            env = dict(os.environ, HOME=directory, XDG_CONFIG_HOME=str(home / 'config space'),
                       XDG_DATA_HOME=str(home / 'data space'))
            subprocess.run([ROOT / 'linux/build.sh', '--install', '--autostart'], env=env,
                           check=True, capture_output=True)
            output = subprocess.check_output([home / '.local/bin/blackout', '--help'], env=env, text=True)
            self.assertIn('--background', output)
            app_dir = home / 'data space/blackout'
            self.assertTrue(all((app_dir / filename).exists() for filename in updater.FILES))
            desktop = home / 'data space/applications/io.github.tacoz234.Blackout.desktop'
            autostart = home / 'config space/autostart/io.github.tacoz234.Blackout.desktop'
            subprocess.run(['desktop-file-validate', desktop, autostart], check=True)
            self.assertIn('--background', autostart.read_text())
            # Reinstallation preserves the saved login choice.
            before = autostart.read_text()
            subprocess.run([ROOT / 'linux/build.sh', '--install'], env=env, check=True, capture_output=True)
            self.assertEqual(autostart.read_text(), before)

    def test_release_has_all_runtime_files_and_stamped_version(self):
        version = '7.8.9-test'
        subprocess.run([ROOT / 'linux/build.sh', '--package'], env=dict(os.environ, VERSION=version),
                       check=True, capture_output=True)
        with tarfile.open(ROOT / f'dist/Blackout-{version}-linux.tar.gz') as archive:
            names = archive.getnames()
            self.assertTrue(all('linux/' + filename in names for filename in updater.FILES))
            self.assertIn('linux/build.sh', names)
            self.assertTrue(archive.getmember('linux/build.sh').mode & 0o111)
            self.assertEqual(archive.extractfile('linux/VERSION').read().decode().strip(), version)


if __name__ == '__main__':
    unittest.main(verbosity=2)
