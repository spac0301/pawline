"""Build and load a small, explicitly installed observer-code package.

The stable relay executable never has to be overwritten for a reducer update.
Packages contain code, not state. Checksums detect incomplete updates; they are
not signatures. Install packages only from a source you trust to run as you.
"""
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

PROTOCOL = 1
LIMIT = 2 * 1024 * 1024
FILES = ('__init__.py', 'identity.py', 'messages.py', 'observation.py', 'live.py',
         'records.py', 'storage.py', 'platform_support.py', 'observer_ipc.py', 'observer_worker.py')


def validate(data):
    if len(data) > LIMIT:
        raise ValueError('observer package exceeds size limit')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        expected = {'fluff_monitor/' + name for name in FILES} | {'manifest.json'}
        if (len(archive.namelist()) != len(expected) or set(archive.namelist()) != expected
                or sum(f.file_size for f in archive.infolist()) > LIMIT):
            raise ValueError('unexpected observer package contents')
        manifest = json.loads(archive.read('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('protocol') != PROTOCOL or set(manifest.get('files', {})) != expected - {'manifest.json'}:
            raise ValueError('incompatible observer package')
        for name, digest in manifest['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError('observer package checksum mismatch')
        return manifest


def read_bundle(path):
    with Path(path).open('rb') as stream:
        data = stream.read(LIMIT + 1)
    try:
        validate(data)
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError) as error:
        raise ValueError('invalid observer package') from error
    return data


def build_bundle(root=None):
    root = Path(root or __file__).resolve()
    if root.is_file():
        root = root.parent
    data = {f'fluff_monitor/{name}': (root / name).read_bytes() for name in FILES}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in data.items():
            archive.writestr(name, content)
        archive.writestr('manifest.json', json.dumps(dict(protocol=PROTOCOL,
            files={name: hashlib.sha256(value).hexdigest() for name, value in data.items()})))
    value = output.getvalue()
    validate(value)
    return value


def load_bundle(directory):
    installed = Path(directory) / 'observer-runtime.zip'
    if installed.is_file():
        return read_bundle(installed)
    if getattr(sys, 'frozen', False):
        return read_bundle(Path(sys.executable).parent / 'observer-runtime.zip')
    return build_bundle()


def install_bundle(path, directory):
    store_bundle(read_bundle(path), directory)


def store_bundle(data, directory):
    from .platform_support import protect_owned_path
    validate(data)
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='.observer-update-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            protect_owned_path(temporary)
            stream.write(data)
        os.replace(temporary, directory / 'observer-runtime.zip')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bootstrap(path):
    """The child's only imported project code comes from its immutable copy."""
    read_bundle(path)
    for name in list(sys.modules):
        if name == 'fluff_monitor' or name.startswith('fluff_monitor.'):
            del sys.modules[name]
    sys.path.insert(0, str(Path(path).resolve()))
    importlib.invalidate_caches()
    module = importlib.import_module('fluff_monitor.observer_worker')
    if not str(module.__file__).startswith(str(Path(path).resolve())):
        raise RuntimeError('observer runtime was not loaded from its package')
    return module.main()
