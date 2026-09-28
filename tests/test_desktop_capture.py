import json
import tempfile
import unittest
import socket
import ssl
import sqlite3
import time
from unittest.mock import patch
from pathlib import Path
from fluff_monitor.capture import DesktopCapture
from fluff_monitor.proxy import WsMessage, CertAuthority, InterceptProxy
from fluff_monitor.views import desktop_view as read_desktop_view
from fluff_monitor.catalog import ThreadCatalog

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"


def send(cap, model, effort, served, index=1, conn=1, kind="response.completed", thread_id=None):
    cap.feed(WsMessage("c2s", json.dumps({"type":"response.create", "model":model,
        "client_metadata": {"thread_id":thread_id},
        "reasoning": {"effort":effort}, "input":[{"role":"user","content":"PRIVATE_PROMPT"}]}), index, conn))
    cap.feed(WsMessage("s2c", json.dumps({"type":kind,"response":{"id":str(index),
        "model":served,"status":"failed" if kind=="response.failed" else "completed",
        "output":[{"text":"PRIVATE_RESPONSE"}]}}), index+.1, conn))


_CAPTURES = {}


def desktop_view(directory, **kwargs):
    cap = _CAPTURES.get(str(directory))
    if cap and not cap.stopping:
        assert cap.flush(), "observer publication barrier timed out"
    return read_desktop_view(directory, **kwargs)


class DesktopTests(unittest.TestCase):
    def capture(self, directory):
        cap = DesktopCapture(directory)
        _CAPTURES[str(directory)] = cap
        self.addCleanup(cap.close)
        self.addCleanup(_CAPTURES.pop, str(directory), None)
        return cap

    def test_interleaved_sessions_pin_and_latest_request(self):
        with tempfile.TemporaryDirectory() as directory:
            cap=self.capture(directory)
            for conn in (1,2): cap.event("ws_open",{"conn":conn})
            for tid,conn,model,effort in ((A,1,"gpt-6-astra","max"),(B,2,"gpt-6-sol","low")):
                cap.feed(WsMessage("c2s",json.dumps({"type":"response.create","model":model,
                    "reasoning":{"effort":effort},"client_metadata":{"thread_id":tid}}),conn,conn))
            self.assertEqual(desktop_view(directory)["thread_id"],B)
            self.assertEqual(desktop_view(directory,thread_id=A)["effort"],"max")
            # A's late completion cannot displace the newer B request or complete B.
            cap.feed(WsMessage("s2c",json.dumps({"type":"response.completed","response":{
                "id":"a","model":"gpt-6-astra","status":"completed"}}),3,1))
            self.assertEqual(desktop_view(directory)["thread_id"],B)
            self.assertEqual(desktop_view(directory)["verdict"],"PENDING")
            self.assertEqual(desktop_view(directory,thread_id=A)["verdict"],"ok")
            cap.feed(WsMessage("s2c",json.dumps({"type":"response.completed","response":{
                "id":"b","model":"gpt-6-sol","status":"completed"}}),4,2))
            self.assertEqual(desktop_view(directory,thread_id=B)["served"],"gpt-6-sol")
            send(cap,"gpt-6-astra","low","gpt-6-astra",5,1,thread_id=A)
            self.assertEqual(desktop_view(directory)["thread_id"],A)
            self.assertEqual(desktop_view(directory,thread_id=A)["effort"],"low")
            self.assertEqual(desktop_view(directory,thread_id=B)["requested"],"gpt-6-sol")
            cap.event("ws_close",{"conn":1})
            self.assertEqual(desktop_view(directory,thread_id=A)["verdict"],"NO_DATA")
            self.assertEqual(desktop_view(directory,thread_id=B)["verdict"],"ok")

    def test_unidentified_or_missing_session_does_not_borrow_another(self):
        with tempfile.TemporaryDirectory() as directory:
            cap=self.capture(directory)
            cap.event("ws_open",{"conn":1})
            send(cap,"gpt-6-astra","max","gpt-6-astra",thread_id=A)
            send(cap,"gpt-6-sol","low","gpt-6-sol",2,thread_id="not-a-thread")
            self.assertIsNone(desktop_view(directory)["thread_id"])
            self.assertEqual(desktop_view(directory,thread_id=A)["requested"],"gpt-6-astra")
            missing=desktop_view(directory,thread_id=B)
            self.assertEqual(missing["verdict"],"NO_DATA")
            self.assertIsNone(missing.get("requested"))
            self.assertEqual(desktop_view(directory,thread_id=B,now=time.time()+10)["verdict"],"UNKNOWN")
            self.assertEqual(len(desktop_view(directory)["sessions"]),1)

    def test_only_observed_thread_titles_are_read(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath=Path(directory)/"state.sqlite"
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE threads (id TEXT,title TEXT,first_user_message TEXT)")
                db.executemany("INSERT INTO threads VALUES (?,?,?)",[(A,"라우팅 펫 구현","PRIVATE"),(B,"다른 작업","SECRET")])
            titles=ThreadCatalog(dbpath).resolve([A])
            self.assertEqual(titles,{A:"라우팅 펫 구현"})
            absent=Path(directory)/"missing.sqlite"
            self.assertEqual(ThreadCatalog(absent).resolve([A]),{})
            self.assertFalse(absent.exists())

    def test_display_name_takes_precedence_over_multiline_first_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath=Path(directory)/"state.sqlite"
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE threads (id TEXT,title TEXT,name TEXT)")
                db.executemany("INSERT INTO threads VALUES (?,?,?)",[
                    (A,"# Context from my IDE setup:\n## Open tabs:\nPRIVATE_PATH\n## My request:\nPRIVATE_PROMPT",
                     "Review codex-routing-detector"),
                    (B,"# Context from my IDE setup:\nPRIVATE_PROMPT",None)])
            titles=ThreadCatalog(dbpath).resolve([A,B])
            self.assertEqual(titles,{A:"Review codex-routing-detector", B:None})

    def test_multiline_custom_name_is_flattened(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath=Path(directory)/"state.sqlite"
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE threads (id TEXT,title TEXT,name TEXT)")
                db.execute("INSERT INTO threads VALUES (?,?,?)",(A,"legacy","  카메라\n보정\t작업\u2028확인  "))
            self.assertEqual(ThreadCatalog(dbpath).resolve([A]),{A:"카메라 보정 작업 확인"})

    def test_task_registration_archive_restore_and_delete_update_the_view(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath=Path(directory)/"state.sqlite"
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY,title TEXT,name TEXT,archived INTEGER)")
                db.execute("INSERT INTO threads VALUES (?,?,?,0)",(A,"legacy","기존 작업"))
            catalog=ThreadCatalog(dbpath)
            cap=self.capture(directory)
            for conn in (1,2): cap.event("ws_open",{"conn":conn})
            send(cap,"gpt-6-astra","max","gpt-6-astra",thread_id=A)
            send(cap,"gpt-6-sol","low","gpt-6-sol",2,2,thread_id=B)
            self.assertTrue(cap.flush())
            raw=Path(directory,"desktop.json").read_bytes()
            view=lambda **kw: desktop_view(directory,catalog=catalog,**kw)
            # The newest transport request is not necessarily a saved app task.
            self.assertEqual(view()["thread_id"],A)
            self.assertEqual([s["thread_id"] for s in view()["sessions"]],[A])
            self.assertTrue(view(thread_id=B)["selection_unavailable"])
            self.assertIsNone(view(thread_id=B).get("served"))
            # Registration can lag the request, and an actual unnamed task is valid.
            with sqlite3.connect(dbpath) as db:
                db.execute("INSERT INTO threads VALUES (?,NULL,NULL,0)",(B,))
            self.assertEqual(view()["thread_id"],A)
            catalog.checked-=6
            self.assertEqual(view()["thread_id"],B)
            self.assertIn(B,catalog.resolve([A,B]))
            self.assertIsNone(view()["title"])
            with sqlite3.connect(dbpath) as db:
                db.execute("UPDATE threads SET name=?,archived=1 WHERE id=?",("새 작업",B))
            catalog.checked-=6
            self.assertEqual(view()["thread_id"],A)
            fixed=view(thread_id=B)
            self.assertTrue(fixed["selection_unavailable"])
            self.assertFalse(fixed["connected"])
            self.assertIsNone(fixed.get("requested"))
            self.assertIsNone(fixed.get("served"))
            self.assertEqual([s["thread_id"] for s in fixed["sessions"]],[A])
            with sqlite3.connect(dbpath) as db:
                db.execute("UPDATE threads SET archived=0 WHERE id=?",(B,))
            catalog.checked-=6
            self.assertEqual(view()["title"],"새 작업")
            self.assertEqual(view(thread_id=B)["effort"],"low")
            with sqlite3.connect(dbpath) as db:
                db.execute("DELETE FROM threads WHERE id=?",(B,))
            catalog.checked-=6
            self.assertEqual(view()["thread_id"],A)
            self.assertTrue(view(thread_id=B)["selection_unavailable"])
            with sqlite3.connect(dbpath) as db:
                db.execute("UPDATE threads SET archived=1")
            catalog.checked-=6
            empty=view()
            self.assertEqual(empty["sessions"],[])
            self.assertIsNone(empty.get("thread_id"))
            self.assertIsNone(empty.get("requested"))
            self.assertEqual(empty["verdict"],"NO_DATA")
            self.assertEqual(Path(directory,"desktop.json").read_bytes(),raw)

    def test_registered_task_without_capture_waits_without_borrowing(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath=Path(directory)/"state.sqlite"
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE threads (id TEXT,title TEXT)")
                db.execute("INSERT INTO threads VALUES (?,?)",(A,"새 작업"))
            catalog=ThreadCatalog(dbpath)
            waiting=desktop_view(directory,thread_id=A,catalog=catalog)
            self.assertEqual(waiting["title"],"새 작업")
            self.assertFalse(waiting["selection_unavailable"])
            self.assertEqual(waiting["label"],"앱 감시 연결 전")
            cap=self.capture(directory)
            cap.event("ws_open",{"conn":1})
            send(cap,"gpt-6-sol","low","gpt-6-sol",thread_id=B)
            waiting=desktop_view(directory,thread_id=A,catalog=catalog)
            self.assertEqual(waiting["title"],"새 작업")
            self.assertEqual(waiting["label"],"선택한 작업 대기")
            self.assertIsNone(waiting.get("requested"))
            self.assertEqual(waiting["sessions"],[])

    def test_catalog_read_error_uses_only_previously_confirmed_tasks_then_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            dbpath=Path(directory)/"state.sqlite"
            with sqlite3.connect(dbpath) as db:
                db.execute("CREATE TABLE threads (id TEXT,title TEXT,archived INTEGER)")
                db.execute("INSERT INTO threads VALUES (?,?,0)",(A,"기존 작업"))
            catalog=ThreadCatalog(dbpath)
            self.assertEqual(catalog.resolve([A]),{A:"기존 작업"})
            with patch("fluff_monitor.catalog.sqlite3.connect",side_effect=sqlite3.OperationalError("busy")):
                self.assertEqual(catalog.resolve([A,B]),{A:"기존 작업"})
                self.assertFalse(catalog.available)
            with sqlite3.connect(dbpath) as db:
                db.execute("UPDATE threads SET archived=1 WHERE id=?",(A,))
                db.execute("INSERT INTO threads VALUES (?,?,0)",(B,"새 작업"))
            catalog.checked-=6
            self.assertEqual(catalog.resolve([A,B]),{B:"새 작업"})
            self.assertTrue(catalog.available)

    def test_unrelated_https_keeps_original_certificate(self):
        from test_transport import EchoServer, recv_head
        upstream_ca, local_ca = CertAuthority(), CertAuthority()
        server = EchoServer(upstream_ca.context_for("localhost"))
        server.start()
        proxy = InterceptProxy(local_ca, on_message=lambda _: self.fail("unrelated traffic decoded"),
                               intercept_hosts={"chatgpt.com"})
        proxy.start()
        try:
            raw = socket.create_connection(("127.0.0.1",proxy.port),timeout=3)
            raw.sendall(f"CONNECT localhost:{server.port} HTTP/1.1\r\n\r\n".encode())
            self.assertIn(b"200",recv_head(raw))
            # Trust only the original server CA, not the detector's CA.
            with ssl.create_default_context(cafile=str(upstream_ca.cert_path)).wrap_socket(raw,server_hostname="localhost") as tls:
                tls.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                self.assertIn(b"200",recv_head(tls))
        finally:
            proxy.stop()
            server.close()
            upstream_ca.close()
            local_ca.close()

    def test_model_effort_switch_no_probe_fallback_and_private_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,"probe.json").write_text('{"served":"old","verdict":"ok"}')
            self.assertEqual(desktop_view(directory)["verdict"],"NO_DATA")
            cap=self.capture(directory)
            cap.event("ws_open",{"conn":1})
            for index,(model,effort) in enumerate((("gpt-6-astra","max"),("gpt-6-sol","low")),1):
                send(cap,model,effort,model,index)
                view=desktop_view(directory)
                self.assertEqual((view["requested"],view["effort"],view["served"],view["verdict"]),
                                 (model,effort,model,"ok"))
            send(cap,"gpt-6-sol",None,"gpt-6-sol",3)
            self.assertIsNone(desktop_view(directory)["effort"])
            send(cap,"gpt-6-astra","max","gpt-6-sol",4)
            self.assertEqual(desktop_view(directory)["verdict"],"REROUTED")
            cap.close()
            self.assertEqual(desktop_view(directory)["verdict"],"UNKNOWN")
            self.assertNotIn("PRIVATE",Path(directory,"desktop.json").read_text())

    def test_capture_loss_never_reuses_a_matching_result(self):
        with tempfile.TemporaryDirectory() as directory:
            cap=self.capture(directory)
            cap.event("ws_open",{"conn":1})
            send(cap,"gpt-6-astra","max","gpt-6-astra")
            self.assertEqual(desktop_view(directory)["verdict"],"ok")
            cap.event("ws_close",{"conn":1})
            self.assertEqual(desktop_view(directory)["verdict"],"NO_DATA")
            cap.event("ws_open",{"conn":1})
            cap.event("parse_lost",{"conn":1})
            self.assertNotEqual(desktop_view(directory)["verdict"],"ok")


if __name__ == "__main__":
    unittest.main()
