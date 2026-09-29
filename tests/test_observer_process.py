"""Only local TLS and synthetic CLI traffic; never a real model or desktop."""
import base64
import io
import json
import os
from pathlib import Path
import queue
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from fluff_monitor import live, observer_runtime, proxy
from fluff_monitor.observer_host import ObserverHost, request_reload
from fluff_monitor.storage import read_snapshot
from test_transport import frame, msg
from test_http_recovery import read_exact

A = '11111111-1111-4111-8111-111111111111'
ROOT = Path(__file__).resolve().parents[1]


def eventually(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(.02)
    raise AssertionError('condition did not become true')


def request(host, conn=1):
    host.feed(msg('c2s', dict(type='response.create', model='fixture',
        client_metadata=dict(thread_id=A), input=[dict(role='user', content='HIDDEN_INPUT')]), conn=conn))


def response(host, conn=1, status='completed'):
    host.feed(msg('s2c', dict(type='response.' + ('created' if status == 'in_progress' else 'completed'),
        response=dict(id=str(conn), model='fixture', status=status,
                      output=[dict(text='HIDDEN_OUTPUT')])), conn=conn))


CLIENT = r'''
import base64,json,os,socket,ssl,sys,urllib.parse
def exact(stream,n):
    b=b''
    while len(b)<n:
        p=stream.recv(n-len(b))
        if not p:raise EOFError()
        b+=p
    return b
def head(stream):
    b=b''
    while not b.endswith(b'\r\n\r\n'):b+=exact(stream,1)
    return b
p=urllib.parse.urlsplit(os.environ['HTTPS_PROXY'])
auth=base64.b64encode((urllib.parse.unquote(p.username)+':'+urllib.parse.unquote(p.password)).encode()).decode()
port=int(os.environ['PAWLINE_TEST_PORT'])
raw=socket.create_connection((p.hostname,p.port),timeout=5)
raw.sendall(f'CONNECT localhost:{port} HTTP/1.1\r\nHost: localhost\r\nProxy-Authorization: Basic {auth}\r\n\r\n'.encode())
assert b'200' in head(raw)
tls=ssl.create_default_context(cafile=os.environ['CODEX_CA_CERTIFICATE']).wrap_socket(raw,server_hostname='localhost')
tls.sendall(b'GET /backend-api/codex/responses HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n')
assert b'101' in head(tls)
print(json.dumps({'pid':os.getpid(),'ready':True}),flush=True)
for line in sys.stdin:
    command=json.loads(line)
    if command['op']=='quit':break
    if command['op']=='send':tls.sendall(base64.b64decode(command['data']));data=b''
    else:data=exact(tls,command['length'])
    print(json.dumps({'pid':os.getpid(),'data':base64.b64encode(data).decode()}),flush=True)
tls.close()
'''


class ObserverProcessTests(unittest.TestCase):
    def test_close_during_worker_creation_leaves_no_worker_or_io_thread(self):
        with tempfile.TemporaryDirectory() as temporary:
            entered, release = threading.Event(), threading.Event()
            children = []
            original = subprocess.Popen
            def delayed(*args, **kwargs):
                child = original(*args, **kwargs)
                children.append(child)
                entered.set()
                if not release.wait(3):raise TimeoutError('fixture startup release')
                return child
            with patch('fluff_monitor.observer_host.subprocess.Popen', side_effect=delayed):
                host = ObserverHost(temporary, rpc_timeout=.3,
                    worker_command=[sys.executable, '-c', 'import time; time.sleep(30)'])
                try:
                    self.assertTrue(entered.wait(2))
                    closer = threading.Thread(target=host.close, daemon=True);closer.start()
                    eventually(lambda: host.closed)
                    release.set()
                    closer.join(timeout=2)
                    self.assertFalse(closer.is_alive())
                    self.assertFalse(host.thread.is_alive())
                    self.assertFalse(host.watchdog.is_alive())
                    self.assertIsNotNone(children[0].poll())
                finally:
                    release.set()
                    for child in children:
                        if child.poll() is None:child.kill()
                        child.wait(timeout=2)

    def test_stalled_worker_and_full_inbox_do_not_delay_or_truncate_wire(self):
        with tempfile.TemporaryDirectory() as temporary:
            host = ObserverHost(temporary, max_items=4, max_bytes=4096, rpc_timeout=.3,
                worker_command=[sys.executable, '-c', 'import time; time.sleep(30)'])
            source, writer = socket.socketpair()
            destination, reader = socket.socketpair()
            reader.settimeout(2)
            transport = proxy.InterceptProxy(None, on_message=host.feed, on_event=host.event)
            pump = threading.Thread(target=transport._pump,
                args=(source, destination, proxy.WsParser(), 'c2s', 1, True), daemon=True)
            wire = frame(b'{"type":"response.create","model":"fixture"}') * 100
            try:
                old = eventually(lambda: host.worker_pid)
                pump.start()
                writer.sendall(wire);writer.shutdown(socket.SHUT_WR)
                self.assertEqual(read_exact(reader, len(wire)), wire)
                pump.join(timeout=2)
                self.assertFalse(pump.is_alive())
                self.assertLessEqual(host.queued_bytes, 4096)
                eventually(lambda: host.worker_pid and host.worker_pid != old, timeout=3)
            finally:
                for sock in (source, writer, destination, reader):sock.close()
                host.close()

    def test_reload_preserves_uncertainty_for_ambiguous_requests(self):
        with tempfile.TemporaryDirectory() as temporary:
            host = ObserverHost(temporary)
            try:
                request(host)
                request(host)
                self.assertTrue(host.flush())
                self.assertTrue(host.reload())
                response(host)
                self.assertTrue(host.flush())
                state = read_snapshot(Path(temporary) / 'desktop.json')
                self.assertIsNone(state['served'])
                self.assertEqual(state['verdict'], 'UNKNOWN')
            finally:
                host.close()

    def test_reload_preserves_live_response_identity_and_loads_updated_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            host = ObserverHost(root)
            try:
                self.assertTrue(host.flush())
                request(host)
                response(host, status='in_progress')
                self.assertTrue(host.flush())
                old = host.worker_pid
                # This is code-package replacement, not just a new process with
                # the same modules. The running host and executable are untouched.
                source = root / 'source'
                source.mkdir()
                for name in observer_runtime.FILES:
                    (source / name).write_bytes((ROOT / 'fluff_monitor' / name).read_bytes())
                (source / '__init__.py').write_text('__version__ = "fixture-new-runtime"\n')
                observer_runtime.store_bundle(observer_runtime.build_bundle(source), root)
                self.assertTrue(host.reload())
                self.assertNotEqual(host.worker_pid, old)
                self.assertEqual(host.version, 'fixture-new-runtime')
                response(host)
                self.assertTrue(host.flush())
                state = read_snapshot(root / 'desktop.json')
                self.assertEqual((state['served'], state['verdict']), ('fixture', 'ok'))
                self.assertFalse(state['capture_lost'])
                for path in root.glob('*.json'):
                    self.assertNotIn('HIDDEN_', path.read_text())
            finally:
                host.close()

    def test_crashed_worker_recovers_without_reusing_inflight_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            host = ObserverHost(temporary)
            try:
                self.assertTrue(host.flush())
                request(host)
                self.assertTrue(host.flush())
                old = host.worker_pid
                host.child.kill()
                eventually(lambda: host.worker_pid and host.worker_pid != old and host.phase == 'ready')
                self.assertFalse(host.feed(msg('s2c', dict(type='response.completed',
                    response=dict(id='late', model='wrong', status='completed')), conn=1)))
                request(host, conn=2)
                response(host, conn=2)
                self.assertTrue(host.flush())
                self.assertEqual(read_snapshot(Path(temporary) / 'desktop.json')['served'], 'fixture')
            finally:
                host.close()

    def test_invalid_package_and_failed_candidate_keep_previous_observer_working(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            host = ObserverHost(root)
            try:
                self.assertTrue(host.flush())
                original = host.worker_pid
                (root / 'observer-runtime.zip').write_bytes(b'not a package')
                self.assertFalse(host.reload())
                self.assertEqual(host.worker_pid, original)
                (root / 'observer-runtime.zip').unlink()
                request(host)
                self.assertTrue(host.flush())
                source = root / 'source';source.mkdir()
                for name in observer_runtime.FILES:
                    (source / name).write_bytes((ROOT / 'fluff_monitor' / name).read_bytes())
                (source / 'observer_worker.py').write_text('raise RuntimeError("broken candidate")\n')
                observer_runtime.store_bundle(observer_runtime.build_bundle(source), root)
                self.assertFalse(host.reload())
                response(host)
                self.assertTrue(host.flush())
                self.assertEqual(read_snapshot(root / 'desktop.json')['served'], 'fixture')
                self.assertEqual(observer_runtime.read_bundle(root / 'observer-runtime.zip'), host.last_good_bundle)
            finally:
                host.close()

    def test_control_command_must_target_current_host_and_have_fresh_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            host = ObserverHost(root)
            try:
                self.assertTrue(host.flush())
                eventually(lambda: (root / 'observer.json').exists())
                old = host.worker_pid
                (root / 'observer-command.json').write_text(json.dumps(
                    dict(op='reload', run_id='previous-host', request='a' * 32)))
                time.sleep(.3)
                self.assertEqual(host.worker_pid, old)
                result = request_reload(root)
                self.assertNotEqual(result['worker_pid'], old)
            finally:
                host.close()
            with self.assertRaises(RuntimeError):
                request_reload(root)

    def test_native_cli_and_same_tls_socket_survive_observer_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / 'native.py';script.write_text(CLIENT)
            state = root / 'state'
            ca = proxy.CertAuthority()
            listener = socket.socket();listener.bind(('127.0.0.1', 0));listener.listen();listener.settimeout(5)
            finish = threading.Event();received = queue.Queue();errors = []
            created = frame(json.dumps(dict(type='response.created', response=dict(
                id='same-connection', model='fixture', status='in_progress'))).encode())
            completed = frame(json.dumps(dict(type='response.completed', response=dict(
                id='same-connection', model='fixture', status='completed', output=[dict(text='HIDDEN_OUTPUT')]))).encode())
            request_wire = frame(json.dumps(dict(type='response.create', model='fixture',
                client_metadata=dict(thread_id=A), input=[dict(role='user',content='HIDDEN_INPUT')])).encode(), mask=True)
            def server():
                try:
                    raw, _ = listener.accept()
                    with ca.context_for('localhost').wrap_socket(raw, server_side=True) as tls:
                        proxy.read_head(tls)
                        tls.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n')
                        received.put(read_exact(tls, len(request_wire)))
                        tls.sendall(created)
                        if not finish.wait(10):raise TimeoutError('fixture completion deadline')
                        tls.sendall(completed)
                        while tls.recv(4096):pass
                except Exception as error:
                    errors.append(type(error).__name__)
            server_thread = threading.Thread(target=server, daemon=True);server_thread.start()
            env = dict(os.environ, FLUFF_STATE_DIR=str(state), CODEX_ROUTING_REAL_CLI=sys.executable,
                FLUFF_DESKTOP_CAPTURE='1', CODEX_ROUTING_CAPTURE_HOST='localhost',
                CODEX_CA_CERTIFICATE=str(ca.cert_path), PAWLINE_TEST_PORT=str(listener.getsockname()[1]))
            for key in list(env):
                if key.lower() in ('https_proxy','http_proxy','all_proxy','no_proxy'):env.pop(key)
            with (root / 'stderr.log').open('wb') as log:
                launcher = getattr(self, 'capture_command', [sys.executable, '-B', str(ROOT / 'run.py'), 'capture'])
                wrapper = subprocess.Popen([*launcher, '-u', str(script), 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=log, env=env, text=True)
                output = queue.Queue()
                def collect():
                    for line in wrapper.stdout:output.put(json.loads(line))
                reader = threading.Thread(target=collect, daemon=True);reader.start()
                def command(value):
                    wrapper.stdin.write(json.dumps(value) + '\n');wrapper.stdin.flush()
                    return output.get(timeout=5)
                try:
                    native = output.get(timeout=5)['pid']
                    command(dict(op='send',data=base64.b64encode(request_wire).decode()))
                    self.assertEqual(received.get(timeout=5), request_wire)
                    first = command(dict(op='read',length=len(created)))
                    self.assertEqual(base64.b64decode(first['data']), created)
                    eventually(lambda: read_snapshot(state / 'desktop.json').get('served') == 'fixture')
                    before = json.loads((state / 'observer.json').read_text())
                    source = root / 'updated-code';source.mkdir()
                    for name in observer_runtime.FILES:
                        (source / name).write_bytes((ROOT / 'fluff_monitor' / name).read_bytes())
                    (source / '__init__.py').write_text('__version__ = "fixture-updated"\n')
                    observer_runtime.store_bundle(observer_runtime.build_bundle(source), state)
                    after = request_reload(state)
                    self.assertEqual(after['relay_pid'], wrapper.pid)
                    self.assertNotEqual(before['worker_pid'], after['worker_pid'])
                    self.assertEqual(after['version'], 'fixture-updated')
                    self.assertIsNone(wrapper.poll())
                    finish.set()
                    last = command(dict(op='read',length=len(completed)))
                    self.assertEqual(last['pid'], native)
                    self.assertEqual(base64.b64decode(last['data']), completed)
                    eventually(lambda: read_snapshot(state / 'desktop.json').get('verdict') == 'ok')
                    self.assertEqual(read_snapshot(state / 'desktop.json')['response_id'], 'same-connection')
                finally:
                    finish.set()
                    if wrapper.poll() is None:
                        wrapper.stdin.write('{"op":"quit"}\n');wrapper.stdin.flush()
                    try:wrapper.wait(timeout=5)
                    except subprocess.TimeoutExpired:wrapper.kill();wrapper.wait(timeout=3)
                    wrapper.stdin.close();wrapper.stdout.close()
                    server_thread.join(timeout=3);listener.close();ca.close()
                self.assertEqual(errors, [])
                self.assertEqual(wrapper.returncode, 0, (root / 'stderr.log').read_text())


if __name__ == '__main__':
    unittest.main()
