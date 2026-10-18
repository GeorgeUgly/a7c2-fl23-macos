"""Fetch and verify the exact upstream release; never execute during setup."""
import hashlib
import importlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / '.upstream'


def lock():
    return json.loads((ROOT / 'upstream.lock.json').read_text())


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify(directory=DEST):
    metadata = lock()
    actual = set()
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise ValueError('Symlink in upstream tree')
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    if actual != set(metadata['files']):
        raise ValueError('Upstream file inventory differs; run setup in a clean checkout')
    for name, expected in metadata['files'].items():
        if digest((directory / name).read_bytes()) != expected:
            raise ValueError('Upstream file checksum mismatch: ' + name)
    return directory / 'a7c2_fl23_preview'


def unpack(raw, directory, metadata):
    if digest(raw) != metadata['sha256']:
        raise ValueError('Upstream archive checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = [item for item in archive.infolist() if not item.is_dir()]
        if len(files) != len(metadata['files']) or {i.filename for i in files} != set(metadata['files']):
            raise ValueError('Unexpected upstream archive inventory')
        if sum(i.file_size for i in files) > 10 * 1024 * 1024:
            raise ValueError('Upstream extraction exceeds size limit')
        for item in files:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename:
                raise ValueError('Unsafe archive path')
            if ((item.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('Archive symlink rejected')
            data = archive.read(item)
            if digest(data) != metadata['files'][item.filename]:
                raise ValueError('Archive member mismatch')
            output = directory / item.filename
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('xb') as stream:
                stream.write(data)


def setup():
    if DEST.exists():
        verify()
        return
    metadata = lock()
    url = metadata['repository'].replace('https://github.com/', 'https://raw.githubusercontent.com/')
    url += '/' + metadata['commit'] + '/' + metadata['archive']
    with urllib.request.urlopen(url, timeout=60) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Download exceeds size limit')
    staging = Path(tempfile.mkdtemp(prefix='.upstream-stage-', dir=ROOT))
    try:
        unpack(raw, staging, metadata)
        verify(staging)
        staging.rename(DEST)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def load():
    sys.dont_write_bytecode = True
    base = verify()
    sys.path.insert(0, str(base))
    core = importlib.import_module('core')
    core.assets()
    app = importlib.import_module('app')
    from .messages import event
    app.event = event
    return app, core, importlib.import_module('transport')
