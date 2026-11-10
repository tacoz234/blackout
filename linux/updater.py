"""Release discovery and verified, rollback-safe per-user Linux updates."""
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent
FILES = ('blackout.py', 'desktop.py', 'updater.py', 'VERSION')
MAX_DOWNLOAD = 16 * 1024 * 1024
API = 'https://api.github.com/repos/tacoz234/blackout/releases/latest'


def current_version():
    try:
        return (ROOT / 'VERSION').read_text().strip()
    except OSError:
        return '0.0.0-dev'


def version_tuple(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.+-]+)?', value):
        raise ValueError('Invalid release version')
    return tuple(map(int, value.split('-')[0].split('+')[0].split('.')))


def fetch(url, limit=MAX_DOWNLOAD):
    if not url.startswith('https://'):
        raise ValueError('An HTTPS download is required')
    request = urllib.request.Request(url, headers={'User-Agent': 'Blackout/' + current_version(),
                                                   'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Release exceeds download size limit')
    return data


def find_newer():
    release = json.loads(fetch(API, 2 * 1024 * 1024))
    version = release['tag_name'].removeprefix('v')
    if release.get('draft') or release.get('prerelease') or version_tuple(version) <= version_tuple(current_version()):
        return None
    for asset in release['assets']:
        if asset['name'] == f'Blackout-{version}-linux.tar.gz':
            digest = asset.get('digest') or ''
            if not re.fullmatch('sha256:[0-9a-fA-F]{64}', digest):
                raise ValueError('GitHub did not publish a SHA-256 digest for the Linux release')
            return {'version': version, 'url': asset['browser_download_url'], 'sha256': digest[7:]}
    return None


def install(release, destination):
    data = fetch(release['url'])
    if hashlib.sha256(data).hexdigest().lower() != release['sha256'].lower():
        raise ValueError('The update failed SHA-256 verification')
    destination = Path(destination)
    with tempfile.TemporaryDirectory(prefix='.blackout-update-', dir=destination.parent) as temporary:
        stage = Path(temporary) / 'new'
        stage.mkdir()
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            for filename in FILES:
                members = [m for m in archive.getmembers() if m.name == 'linux/' + filename]
                if len(members) != 1 or not members[0].isfile() or members[0].size > 2 * 1024 * 1024:
                    raise ValueError('Incomplete or unsafe Linux release archive')
                with archive.extractfile(members[0]) as source:
                    (stage / filename).write_bytes(source.read())
        if (stage / 'VERSION').read_text().strip() != release['version']:
            raise ValueError('Release version does not match its contents')
        for filename in FILES:
            if filename.endswith('.py'):
                compile((stage / filename).read_bytes(), filename, 'exec')
        backup = Path(temporary) / 'previous'
        destination.rename(backup)
        try:
            stage.rename(destination)
        except BaseException:
            backup.rename(destination)
            raise
