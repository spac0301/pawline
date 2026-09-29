import json
from pathlib import Path
import runpy
import socket
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from fluff_monitor import proxy
from fluff_monitor.storage import atomic_json
from fluff_monitor import capture
from test_transport import frame, recv_head, deflate_msg


class SecurityTests(unittest.TestCase):
    def test_existing_proxy_is_not_silently_overwritten(self):
        with patch.dict('os.environ',{'CODEX_ROUTING_REAL_CLI':sys.executable,'FLUFF_DESKTOP_CAPTURE':'1','HTTPS_PROXY':'http://example.invalid:3128'},clear=True),patch.object(capture,'ObserverHost') as monitor,patch.object(capture.subprocess,'Popen') as child:
            self.assertEqual(capture.main(['app-server']),1)
            monitor.assert_not_called();child.assert_not_called()

    def test_unauthed_proxy_client_is_rejected_before_any_upstream_connection(self):
        ca=proxy.CertAuthority()
        server=proxy.InterceptProxy(ca,on_message=lambda _:None,require_auth=True)
        server.start()
        try:
            self.assertEqual(server._server.server_address[0],'127.0.0.1')
            with patch('fluff_monitor.proxy.socket.create_connection') as upstream:
                for auth in ('','Proxy-Authorization: Basic aW52YWxpZA==\r\n'):
                    with socket.socket() as client:
                        client.settimeout(2);client.connect(('127.0.0.1',server.port))
                        client.sendall(('CONNECT example.invalid:443 HTTP/1.1\r\n'+auth+'\r\n').encode())
                        self.assertIn(b'407 Proxy Authentication Required',recv_head(client))
                upstream.assert_not_called()
            self.assertTrue(server.auth_token)
        finally:server.stop();ca.close()

    @unittest.skipIf(sys.platform == 'win32', 'Windows ACLs have their own native test')
    def test_certificate_keys_and_snapshots_are_private_and_ca_is_removed(self):
        ca=proxy.CertAuthority();root=ca.dir
        try:
            ca.context_for('localhost')
            self.assertEqual(stat.S_IMODE(root.stat().st_mode),0o700)
            for path in root.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        finally:ca.close()
        self.assertFalse(root.exists())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'snapshot.json';atomic_json(path,dict(model='synthetic'))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)

    def test_compressed_message_limit_applies_after_inflation(self):
        compressed=deflate_msg(zlib.compressobj(9,zlib.DEFLATED,-15),b'x'*2048)
        with patch.object(proxy,'MAX_WS_MESSAGE',1024):
            self.assertLess(len(compressed),1024)
            with self.assertRaisesRegex(proxy.WsError,'decoded message'):
                proxy.WsParser(deflate=True).feed(frame(compressed,rsv1=True))

    def test_unsupported_platform_is_rejected_before_platform_specific_imports_or_startup(self):
        root=Path(__file__).resolve().parents[1]
        with patch.object(sys,'platform','darwin'),patch.object(sys,'argv',[str(root/'run.py'),'pet']),patch('importlib.import_module') as imported:
            with self.assertRaisesRegex(SystemExit,'platform is not supported'):
                runpy.run_path(str(root/'run.py'),run_name='__main__')
            imported.assert_not_called()
