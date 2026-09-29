"""Stable, bounded pipe bridge. Its child owns metadata; never relay sockets.

Only the IO thread touches the worker protocol. The watchdog may kill that
worker on a deadline, which releases a blocked pipe without touching Codex.
"""
from collections import deque
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from . import observer_runtime, storage
from .observer_ipc import PROTOCOL, read_packet, write_packet
from .platform_support import lock_exclusive, protect_owned_path

MAX_ITEMS = 512
MAX_BYTES = 16 * 1024 * 1024
RPC_TIMEOUT = 5.0


class ObserverHost:
    def __init__(self, directory=None, *, max_items=MAX_ITEMS, max_bytes=MAX_BYTES,
                 rpc_timeout=RPC_TIMEOUT, worker_command=None):
        self.directory = Path(directory or storage.state_dir())
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = (self.directory / 'observer-host.lock').open('a+b')
        protect_owned_path(self.directory / 'observer-host.lock')
        if not lock_exclusive(self.lock):
            self.lock.close()
            raise RuntimeError('An observer host is already active')
        self.condition = threading.Condition()
        self.inbox = deque()
        self.max_items, self.max_bytes, self.queued_bytes = max_items, max_bytes, 0
        self.accepted = self.processed = self.flushed = self.flush_requested = 0
        self.last_connection = self.invalid_through = -1
        self.pending_loss = None
        self.closed = False
        self.child = None
        self.rpc_deadline = 0
        self.rpc_timeout = rpc_timeout
        self.worker_command = worker_command
        self.runtime_copy = None
        self.last_good_bundle = None
        self.run_id = uuid.uuid4().hex
        self.generation = 0
        self.phase, self.version = 'starting', None
        self.reload_request = self.completed_request = self.reload_error = None
        self.last_control = None
        self.thread = threading.Thread(target=self._run, name='pawline-observer-io', daemon=True)
        self.watchdog = threading.Thread(target=self._watch, name='pawline-observer-watch', daemon=True)
        self.thread.start()
        self.watchdog.start()

    @property
    def worker_pid(self):
        child = self.child
        return child.pid if child and child.poll() is None else None

    def _lose_locked(self, reason):
        self.invalid_through = self.last_connection
        self.accepted += 1
        self.pending_loss = (self.accepted, self.invalid_through, reason)
        self.inbox.clear()
        self.queued_bytes = 0
        self.condition.notify_all()

    def _enqueue(self, header, payload, conn, size):
        with self.condition:
            if self.closed or conn <= self.invalid_through:
                return False
            self.last_connection = max(self.last_connection, conn)
            if len(self.inbox) >= self.max_items or self.queued_bytes + size > self.max_bytes:
                self._lose_locked('observation_queue_limit')
                return False
            self.accepted += 1
            self.inbox.append((self.accepted, header, payload, size))
            self.queued_bytes += size
            self.condition.notify_all()
            return True

    def feed(self, message):
        data = message.text.encode('utf-8') if isinstance(message.text, str) else message.text
        header = dict(op='message', direction=message.direction, ts=message.ts,
                      conn=message.conn, transport=message.transport)
        return self._enqueue(header, data, message.conn, len(data) + 256)

    def event(self, kind, info):
        if kind not in ('ws_open', 'ws_close', 'http_open', 'http_close', 'parse_lost'):
            return True
        return self._enqueue(dict(op='event', kind=kind, info=dict(info)), b'',
                             info['conn'], len(json.dumps(info).encode()) + 256)

    def flush(self, timeout=5):
        end = time.monotonic() + timeout
        with self.condition:
            target = self.accepted
            self.flush_requested = max(self.flush_requested, target)
            self.condition.notify_all()
            while self.flushed < target or self.phase != 'ready':
                left = end - time.monotonic()
                if self.closed or left <= 0:
                    return False
                self.condition.wait(left)
            return True

    def reload(self, timeout=12):
        request = uuid.uuid4().hex
        end = time.monotonic() + timeout
        with self.condition:
            self.reload_request = request
            self.condition.notify_all()
            while self.completed_request != request:
                left = end - time.monotonic()
                if self.closed or left <= 0:
                    return False
                self.condition.wait(left)
            return self.reload_error is None

    def _rpc(self, header, payload=b''):
        self.rpc_deadline = time.monotonic() + self.rpc_timeout
        try:
            write_packet(self.child.stdin, header, payload)
            response, data = read_packet(self.child.stdout)
            if not response.get('ok'):
                raise RuntimeError('observer rejected command')
            return response, data
        finally:
            self.rpc_deadline = 0

    def _spawn(self, bundle, checkpoint=b''):
        observer_runtime.validate(bundle)
        self.runtime_copy = tempfile.TemporaryDirectory(prefix='.observer-', dir=self.directory)
        protect_owned_path(self.runtime_copy.name, directory=True)
        archive = Path(self.runtime_copy.name) / 'runtime.zip'
        archive.write_bytes(bundle)
        protect_owned_path(archive)
        if self.worker_command:
            command = [*self.worker_command, str(archive)]
        elif getattr(sys, 'frozen', False):
            command = [sys.executable, '--fluff-observer-worker', str(archive)]
        else:
            command = [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'run.py'),
                       'observer-worker', str(archive)]
        # A worker has no need for provider credentials, the proxy secret,
        # the native app's stdio, or its inherited configuration overrides.
        allowed = {'PATH', 'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'SYSTEMROOT',
                   'WINDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL'}
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env.update(PYTHONNOUSERSITE='1', PYTHONUTF8='1', PYINSTALLER_RESET_ENVIRONMENT='1')
        self.child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, bufsize=0, close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        response, _ = self._rpc(dict(op='start', protocol=PROTOCOL,
                                    directory=str(self.directory)), checkpoint)
        ready, _ = self._rpc(dict(op='flush'))
        if not ready.get('accepted'):
            raise RuntimeError('observer snapshot unavailable')
        self.version = response['version']
        self.last_good_bundle = bundle
        self.generation += 1
        self.phase = 'ready'
        with self.condition:
            self.condition.notify_all()

    def _dispose(self):
        child, self.child = self.child, None
        if child:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
            child.stdin.close()
            child.stdout.close()
        if self.runtime_copy:
            self.runtime_copy.cleanup()
            self.runtime_copy = None

    def _replace(self, request):
        checkpoint = b''
        previous = self.last_good_bundle
        try:
            bundle = observer_runtime.load_bundle(self.directory)
            _, checkpoint = self._rpc(dict(op='handoff'))
            self.phase = 'reloading'
            self._dispose()
            self._spawn(bundle, checkpoint)
            self.reload_error = None
        except Exception:
            # A valid old worker is left running when validation failed before
            # handoff. If the candidate failed, restore the previous package.
            if checkpoint:
                self._dispose()
                self._spawn(previous, checkpoint)
                observer_runtime.store_bundle(previous, self.directory)
            self.reload_error = 'observer_update_failed'
        with self.condition:
            self.completed_request = request
            self.condition.notify_all()

    def _unavailable(self, reason, active=True):
        storage.Publisher(self.directory).publish('desktop', dict(source='desktop',
            active=active, connected=False, sessions=[], session_protocol=1,
            requested=None, served=None, verdict='UNKNOWN', observation_disabled=reason), heartbeat=True)

    def _run(self):
        next_ping = 0
        try:
            while not self.closed:
                try:
                    if not self.child:
                        self._spawn(observer_runtime.load_bundle(self.directory))
                    with self.condition:
                        loss, self.pending_loss = self.pending_loss, None
                        request = self.reload_request
                        item = None if loss or request != self.completed_request else (
                            self.inbox.popleft() if self.inbox else None)
                        if item:
                            self.queued_bytes -= item[3]
                    if loss:
                        sequence, through, reason = loss
                        self._rpc(dict(op='invalidate', through=through, reason=reason))
                        self.processed = max(self.processed, sequence)
                    elif request != self.completed_request:
                        self._replace(request)
                    elif item:
                        sequence, header, payload, _ = item
                        response, _ = self._rpc(header, payload)
                        with self.condition:
                            self.invalid_through = max(self.invalid_through, response['invalid_through'])
                        self.processed = max(self.processed, sequence)
                    now = time.monotonic()
                    if now >= next_ping or self.processed >= self.flush_requested > self.flushed:
                        reply, _ = self._rpc(dict(op='flush'))
                        if reply.get('accepted'):
                            with self.condition:
                                self.flushed = self.processed
                                self.condition.notify_all()
                        next_ping = now + 1
                    with self.condition:
                        if not self.inbox and not self.pending_loss and self.reload_request == self.completed_request:
                            self.condition.wait(.2)
                except Exception:
                    self.phase = 'recovering'
                    self._dispose()
                    with self.condition:
                        self._lose_locked('observer_worker_unavailable')
                    try:
                        self._unavailable('observer_worker_unavailable')
                    except OSError:
                        pass
                    with self.condition:
                        if not self.closed:
                            self.condition.wait(1)
        finally:
            self._dispose()
            try:
                self._unavailable('observer_stopped', active=False)
            except OSError:
                pass

    def _watch(self):
        next_status = 0
        while not self.closed:
            now = time.monotonic()
            child = self.child
            if self.rpc_deadline and now > self.rpc_deadline and child and child.poll() is None:
                try:
                    child.kill()
                except OSError:
                    pass
            command = storage.read_json(self.directory / 'observer-command.json')
            request = command.get('request')
            if (command.get('run_id') == self.run_id and command.get('op') == 'reload'
                    and isinstance(request, str) and len(request) == 32 and request != self.last_control):
                self.last_control = request
                with self.condition:
                    self.reload_request = request
                    self.condition.notify_all()
            if now >= next_status:
                try:
                    storage.atomic_json(self.directory / 'observer.json', dict(protocol=PROTOCOL,
                        run_id=self.run_id, relay_pid=os.getpid(), worker_pid=self.worker_pid,
                        phase=self.phase, version=self.version, generation=self.generation,
                        completed_request=self.completed_request, error=self.reload_error,
                        updated_at=time.time()))
                except OSError:
                    pass
                next_status = now + 1
            with self.condition:
                self.condition.wait(.2)

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        child = self.child
        if child and child.poll() is None:
            try:
                child.kill()
            except OSError:
                pass
        self.thread.join(timeout=self.rpc_timeout + 4)
        self.watchdog.join(timeout=2)
        storage.atomic_json(self.directory / 'observer.json', dict(protocol=PROTOCOL,
            run_id=self.run_id, phase='stopped', updated_at=time.time()))
        self.lock.close()


def request_reload(directory=None, timeout=12):
    directory = Path(directory or storage.state_dir())
    status = storage.read_json(directory / 'observer.json')
    stamp = status.get('updated_at')
    if (status.get('protocol') != PROTOCOL or status.get('phase') == 'stopped'
            or type(stamp) not in (int, float) or not 0 <= time.time() - stamp < 5):
        raise RuntimeError('No reload-capable observer is active. Older adapters need one normal Codex launch.')
    request = uuid.uuid4().hex
    storage.atomic_json(directory / 'observer-command.json',
                        dict(op='reload', run_id=status['run_id'], request=request))
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = storage.read_json(directory / 'observer.json')
        if result.get('run_id') != status['run_id']:
            raise RuntimeError('The observer host changed during reload')
        if result.get('completed_request') == request:
            if result.get('error'):
                raise RuntimeError(result['error'])
            return result
        time.sleep(.1)
    raise TimeoutError('Observer reload did not complete')
