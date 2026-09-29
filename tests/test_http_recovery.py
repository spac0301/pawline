"""Local TLS only: failed WebSocket -> compressed HTTP/SSE -> fresh metadata."""
import gzip
import base64
import hashlib
import json
from pathlib import Path
import queue
import socket
import ssl
import tempfile
import threading
import time
import unittest
import zlib
import zstandard

from fluff_monitor import proxy
from fluff_monitor.capture import DesktopCapture
from fluff_monitor.http_stream import Events, forward_response
from fluff_monitor.views import desktop_view
from test_transport import frame,recv_head

A='11111111-1111-4111-8111-111111111111'
B='22222222-2222-4222-8222-222222222222'


def event(response_id,model,status):
    return ('data: '+json.dumps(dict(type='response.'+('created' if status=='in_progress' else 'completed'),
        response=dict(id=response_id,model=model,status=status,output=[dict(text='PRIVATE_RESPONSE 한글')])) ,ensure_ascii=False)+'\r\n\r\n').encode()


def wire(body,encoding='identity'):
    if encoding=='gzip':body=gzip.compress(body,mtime=0)
    elif encoding=='zstd':body=zstandard.ZstdCompressor().compress(body)
    head=(f'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Encoding: {encoding}\r\nTransfer-Encoding: chunked\r\nConnection: keep-alive\r\n\r\n').encode()
    # Irregular chunk boundaries exercise HTTP framing and split SSE UTF-8.
    chunks=[body[i:i+17] for i in range(0,len(body),17)]
    return head+b''.join(f'{len(c):x}\r\n'.encode()+c+b'\r\n' for c in chunks)+b'0\r\n\r\n'


class Server:
    def __init__(self,context,responses):
        self.context,self.responses=context,responses
        self.received=[];self.errors=[];self.closed=False
        self.sock=socket.socket();self.sock.bind(('127.0.0.1',0));self.sock.listen();self.sock.settimeout(.1)
        self.port=self.sock.getsockname()[1]
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        while not self.closed:
            try:raw,_=self.sock.accept()
            except socket.timeout:continue
            except OSError:return
            try:
                with self.context.wrap_socket(raw,server_side=True) as tls:
                    while True:
                        try:head,rest=proxy.read_head(tls)
                        except ConnectionError:break
                        line,headers=proxy.parse_head(head)
                        if 'websocket' in headers.get('upgrade','').lower():
                            import base64
                            accept=base64.b64encode(hashlib.sha1((headers['sec-websocket-key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
                            tls.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n')
                            if not rest:tls.recv(65536)
                            break  # Reproduce lost WS before a server model is observed.
                        size=int(headers.get('content-length','0'));body=rest
                        while len(body)<size:body+=tls.recv(size-len(body))
                        self.received.append(body)
                        tls.sendall(self.responses[len(self.received)-1])
            except OSError:pass
            except Exception as exc:self.errors.append(repr(exc))
    def close(self):
        self.closed=True;self.sock.close();self.thread.join(timeout=2)


def connect(port,server_port,ca,token=None):
    raw=socket.create_connection(('127.0.0.1',port),timeout=3)
    auth='Proxy-Authorization: Basic '+base64.b64encode(('fluff:'+token).encode()).decode()+'\r\n' if token else ''
    raw.sendall(f'CONNECT localhost:{server_port} HTTP/1.1\r\nHost: localhost\r\n{auth}\r\n'.encode())
    assert b'200' in recv_head(raw)
    return ssl.create_default_context(cafile=str(ca.cert_path)).wrap_socket(raw,server_hostname='localhost')


def read_exact(sock,n):
    data=b''
    while len(data)<n:
        piece=sock.recv(n-len(data))
        if not piece:raise AssertionError('wire response truncated')
        data+=piece
    return data


class HTTPRecoveryTests(unittest.TestCase):
    def test_queue_loss_recovers_on_next_http_exchange_without_restarting_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = DesktopCapture(tmp, max_bytes=8192)
            server_ca, client_ca = proxy.CertAuthority(), proxy.CertAuthority()
            replies = [wire(event('lost', 'old', 'completed')),
                       wire(event('fresh', 'fresh', 'completed'), 'gzip')]
            server = Server(server_ca.context_for('localhost'), replies)
            transport = proxy.InterceptProxy(client_ca, on_message=cap.feed, on_event=cap.event,
                upstream_context=ssl.create_default_context(cafile=str(server_ca.cert_path)),
                require_auth=True)
            transport.start()
            tls = None
            try:
                tls = connect(transport.port, server.port, client_ca, transport.auth_token)
                expected = []
                for index, model in enumerate(('old', 'fresh')):
                    body = json.dumps(dict(model=model, client_metadata=dict(thread_id=A),
                        input=[dict(role='user', content='PRIVATE_' * (2000 if index == 0 else 1))])).encode()
                    expected.append(body)
                    tls.sendall((f'POST /backend-api/codex/responses HTTP/1.1\r\nHost: localhost\r\n'
                        f'Content-Length: {len(body)}\r\nConnection: keep-alive\r\n\r\n').encode() + body)
                    self.assertEqual(read_exact(tls, len(replies[index])), replies[index])
                    if index == 0:
                        self.assertTrue(cap.flush())
                        value = desktop_view(tmp, thread_id=A)
                        self.assertEqual(value['observation_disabled'], 'observation_queue_limit')
                        self.assertIsNone(value['served'])
                    else:
                        end = time.monotonic() + 2
                        while time.monotonic() < end:
                            value = cap.agg.current(A)
                            if value.get('status') == 'completed' and not value.get('connected'):
                                break
                            time.sleep(.01)
                        self.assertTrue(cap.flush())
                        value = desktop_view(tmp, thread_id=A)
                        self.assertIsNone(value['observation_disabled'])
                        self.assertEqual(value['served'], 'fresh')
                        self.assertEqual(value['verdict'], 'ok')
                self.assertEqual(server.received, expected)
                self.assertEqual(server.errors, [])
                self.assertNotIn('PRIVATE_', (Path(tmp) / 'desktop.json').read_text())
            finally:
                if tls:
                    tls.close()
                transport.stop()
                server.close()
                server_ca.close()
                client_ca.close()
                cap.close()

    def test_incremental_sse_is_visible_before_completion_and_observer_failure_keeps_wire(self):
        source,writer=socket.socketpair();relay,reader=socket.socketpair()
        reader.settimeout(2)
        values=[];seen=threading.Event();lost=[];errors=[]
        def emit(value):
            values.append(value);seen.set()
        def run():
            try:forward_response(relay,source,'POST',emit,lambda:lost.append(True))
            except Exception as error:errors.append(repr(error))
        worker=threading.Thread(target=run,daemon=True);worker.start()
        header=b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n'
        def chunk(body):return f'{len(body):x}\r\n'.encode()+body+b'\r\n'
        first=header+chunk(event('stream','gpt-6-astra','in_progress'))
        # A malformed observation must not terminate or rewrite the real stream.
        second=chunk(b'data: not-json\n\n')+chunk(event('stream','gpt-6-astra','completed'))+b'0\r\n\r\n'
        try:
            writer.sendall(first)
            self.assertEqual(read_exact(reader,len(first)),first)
            self.assertTrue(seen.wait(1));self.assertTrue(worker.is_alive())
            writer.sendall(second)
            self.assertEqual(read_exact(reader,len(second)),second)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(len(values),1);self.assertEqual(lost,[True])
        finally:
            for sock in (writer,source,relay,reader):sock.close()

    def test_interim_http_response_falls_back_to_transparent_relay(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap=DesktopCapture(tmp);server_ca=proxy.CertAuthority();client_ca=proxy.CertAuthority()
            reply=b'HTTP/1.1 103 Early Hints\r\nLink: </asset>\r\n\r\n'+wire(event('end','gpt-6-astra','completed'))
            server=Server(server_ca.context_for('localhost'),[reply])
            transport=proxy.InterceptProxy(client_ca,on_message=cap.feed,on_event=cap.event,
                upstream_context=ssl.create_default_context(cafile=str(server_ca.cert_path)),require_auth=True)
            transport.start();tls=None
            try:
                tls=connect(transport.port,server.port,client_ca,transport.auth_token)
                body=json.dumps(dict(model='gpt-6-astra',client_metadata=dict(thread_id=A))).encode()
                tls.sendall(f'POST /backend-api/codex/responses HTTP/1.1\r\nHost: localhost\r\nContent-Length: {len(body)}\r\n\r\n'.encode()+body)
                self.assertEqual(read_exact(tls,len(reply)),reply)
                end=time.monotonic()+1
                while not cap.agg.current(A).get('capture_lost') and time.monotonic()<end:time.sleep(.01)
                self.assertTrue(cap.agg.current(A)['capture_lost'])
                self.assertIsNone(cap.agg.current(A).get('served'))
            finally:
                if tls:tls.close()
                transport.stop();server.close();server_ca.close();client_ca.close();cap.close()

    def test_sse_split_utf8_multiline_and_crlf(self):
        values=[];parser=Events(values.append)
        data=': comment\r\ndata: {"type":\r\ndata: "한글"}\r\n\r\ndata: [DONE]\r\n\r\n'.encode()
        for byte in data:parser.feed(bytes([byte]))
        parser.feed(b'',final=True)
        self.assertEqual(values,[{'type':'한글'}])

    def test_failed_ws_then_two_keepalive_http_requests_preserve_wire_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap=DesktopCapture(tmp);server_ca=proxy.CertAuthority();client_ca=proxy.CertAuthority()
            replies=[wire(event('r1','gpt-6-astra','in_progress')+event('r1','gpt-6-astra','completed'),'gzip'),
                     wire(event('r2','gpt-6-sol','in_progress')+event('r2','gpt-6-sol','completed'),'zstd')]
            server=Server(server_ca.context_for('localhost'),replies)
            transport=proxy.InterceptProxy(client_ca,on_message=cap.feed,on_event=cap.event,
                upstream_context=ssl.create_default_context(cafile=str(server_ca.cert_path)),require_auth=True)
            transport.start();tls=None
            try:
                tls=connect(transport.port,server.port,client_ca,transport.auth_token)
                tls.sendall(b'GET /backend-api/codex/responses HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n')
                self.assertIn(b'101',recv_head(tls))
                tls.sendall(frame(json.dumps(dict(type='response.create',model='gpt-6-astra',client_metadata=dict(thread_id=A))).encode(),mask=True))
                while tls.recv(100):pass
                tls.close();tls=connect(transport.port,server.port,client_ca,transport.auth_token)
                expected=[]
                for index,(tid,model) in enumerate([(A,'gpt-6-astra'),(B,'gpt-6-sol')]):
                    request=json.dumps(dict(model=model,client_metadata=dict(thread_id=tid),reasoning=dict(effort='max'),
                        stream=True,input=[dict(role='user',content='PRIVATE_PROMPT')])).encode()
                    body=zstandard.ZstdCompressor().compress(request);expected.append(body)
                    head=(f'POST /backend-api/codex/responses HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Encoding: zstd\r\nContent-Length: {len(body)}\r\nConnection: keep-alive\r\n\r\n').encode()
                    tls.sendall(head+body)
                    self.assertEqual(read_exact(tls,len(replies[index])),replies[index])
                    end=time.monotonic()+2
                    while time.monotonic()<end:
                        value=cap.agg.current(tid)
                        if value.get('status')=='completed' and not value.get('connected'):break
                        time.sleep(.01)
                    self.assertEqual((value['requested'],value['served'],value['transport'],value['verdict']),(model,model,'http','ok'))
                self.assertEqual(server.received,expected);self.assertEqual(server.errors,[])
                self.assertTrue(cap.flush())
                text=(Path(tmp)/'desktop.json').read_text()
                self.assertNotIn('PRIVATE_',text)
                self.assertNotIn('Authorization',text)
                for tid in (A,B):
                    view=desktop_view(tmp,thread_id=tid)
                    self.assertEqual(view['label'],'HTTP 응답 기록')
                    self.assertEqual(view['verdict'],'ok')
            finally:
                if tls:tls.close()
                transport.stop();server.close();server_ca.close();client_ca.close();cap.close()


if __name__=='__main__':unittest.main()
