"""Fault and long-session contracts for observation without live app traffic."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from fluff_monitor.capture import DesktopCapture
from fluff_monitor import live, proxy
from fluff_monitor.http_stream import Events, forward_response
from fluff_monitor.views import desktop_view
from test_transport import frame, msg

A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'


class ObserverResilienceTests(unittest.TestCase):
    def relay(self, consumer, event=None, malformed=False, direction='s2c'):
        source, writer = socket.socketpair()
        destination, reader = socket.socketpair()
        reader.settimeout(2)
        transport = proxy.InterceptProxy(None, on_message=consumer, on_event=event)
        worker = threading.Thread(target=transport._pump,
            args=(source, destination, proxy.WsParser(), direction, 1, True), daemon=True)
        wire = frame(b'{"type":"response.create","model":"fixture"}', rsv1=malformed)
        worker.start()
        try:
            writer.sendall(wire)
            writer.shutdown(socket.SHUT_WR)
            received = bytearray()
            while True:
                data = reader.recv(4096)
                if not data:
                    break
                received.extend(data)
            self.assertEqual(bytes(received), wire)
            worker.join(2)
            self.assertFalse(worker.is_alive())
        finally:
            for sock in (source, writer, destination, reader):
                sock.close()

    def test_message_and_loss_callback_failures_never_truncate_either_direction(self):
        def fail(*args):
            raise OSError('synthetic private snapshot storage failure')
        for direction in ('c2s', 's2c'):
            with self.subTest(direction=direction):
                self.relay(fail, fail, direction=direction)
                self.relay(lambda _: None, fail, malformed=True, direction=direction)

    def test_blocked_publication_and_queue_overflow_do_not_block_relay(self):
        with tempfile.TemporaryDirectory() as directory:
            cap = DesktopCapture(directory, max_items=2, max_bytes=4096)
            self.assertTrue(cap.flush())
            entered, release = threading.Event(), threading.Event()
            original = cap.publisher.publish
            def blocked(*args, **kwargs):
                entered.set()
                if not release.wait(2):
                    raise OSError('test publication timeout')
                return original(*args, **kwargs)
            try:
                with patch.object(cap.publisher, 'publish', side_effect=blocked):
                    cap.event('ws_open', {'conn': 1})
                    flush_thread = threading.Thread(target=cap.flush, daemon=True)
                    flush_thread.start()
                    self.assertTrue(entered.wait(1))
                    for _ in range(4):
                        self.relay(cap.feed, cap.event, direction='c2s')
                    self.assertEqual(cap.disabled_reason, 'observation_queue_limit')
                    self.assertLessEqual(cap.queued_bytes, 4096)
                    release.set()
                    flush_thread.join(2)
                self.assertTrue(cap.flush())
                value = json.loads(Path(directory, 'desktop.json').read_text())
                self.assertEqual(value['observation_disabled'], 'observation_queue_limit')
                self.assertEqual(value['verdict'], 'UNKNOWN')
                self.assertIsNone(value['served'])
                # The adapter is still alive: a new connection can observe a
                # complete request/response instead of needing a CLI restart.
                self.assertFalse(cap.feed(msg('s2c', dict(type='response.completed',
                    response=dict(id='late', model='old', status='completed')), conn=1)))
                self.assertTrue(cap.event('ws_open', dict(conn=2)))
                self.assertTrue(cap.feed(msg('c2s', dict(type='response.create',
                    model='fresh', client_metadata=dict(thread_id=B)), conn=2)))
                self.assertTrue(cap.flush())
                self.assertTrue(cap.feed(msg('s2c', dict(type='response.completed',
                    response=dict(id='new', model='fresh', status='completed')), conn=2)))
                self.assertTrue(cap.flush())
                value = desktop_view(directory, thread_id=B)
                self.assertIsNone(value['observation_disabled'])
                self.assertEqual(value['verdict'], 'ok')
                self.assertEqual(value['served'], 'fresh')
            finally:
                release.set()
                cap.close()

    def test_loss_before_first_accepted_message_publishes_and_recovers_repeatedly(self):
        with tempfile.TemporaryDirectory() as directory:
            cap = DesktopCapture(directory, max_bytes=4096)
            try:
                self.assertTrue(cap.flush())
                for conn in range(1, 7, 2):
                    self.assertFalse(cap.feed(msg('c2s', dict(type='response.create',
                        model='lost', input='PRIVATE_' * 1000), conn=conn)))
                    self.assertTrue(cap.flush())
                    value = json.loads(Path(directory, 'desktop.json').read_text())
                    self.assertEqual(value['observation_disabled'], 'observation_queue_limit')
                    self.assertIsNone(value['served'])
                    self.assertGreater(value['last_observation_loss']['incoming_bytes'], 4096)
                    self.assertNotIn('PRIVATE_', json.dumps(value))
                    # Frames from any previous generation stay rejected, even
                    # after the global disabled indication has cleared.
                    self.assertTrue(cap.feed(msg('c2s', dict(type='response.create',
                        model='fresh', client_metadata=dict(thread_id=B)), conn=conn + 1)))
                    self.assertTrue(cap.feed(msg('s2c', dict(type='response.completed',
                        response=dict(id=str(conn), model='fresh', status='completed')), conn=conn + 1)))
                    self.assertTrue(cap.flush())
                    self.assertFalse(cap.event('ws_open', dict(conn=1)))
                    self.assertFalse(cap.feed(msg('s2c', dict(type='response.completed',
                        response=dict(id='late', model='wrong', status='completed')), conn=1)))
                    value = desktop_view(directory, thread_id=B)
                    self.assertIsNone(value['observation_disabled'])
                    self.assertEqual(value['served'], 'fresh')
                    self.assertEqual(value['verdict'], 'ok')
            finally:
                cap.close()

    def test_recovery_does_not_restore_a_different_sessions_lost_response(self):
        with tempfile.TemporaryDirectory() as directory:
            cap = DesktopCapture(directory, max_bytes=4096)
            try:
                cap.feed(msg('c2s', dict(type='response.create', model='old',
                    client_metadata=dict(thread_id=A)), conn=1))
                cap.feed(msg('s2c', dict(type='response.completed', response=dict(
                    id='old', model='old', status='completed')), conn=1))
                self.assertTrue(cap.flush())
                self.assertEqual(desktop_view(directory, thread_id=A)['served'], 'old')
                self.assertFalse(cap.feed(msg('c2s', dict(input='x' * 5000), conn=1)))
                # Recovery can be queued before the worker handles the loss.
                cap.event('http_open', dict(conn=2))
                cap.feed(msg('c2s', dict(type='response.create', model='fresh',
                    client_metadata=dict(thread_id=B)), conn=2))
                cap.feed(msg('s2c', dict(type='response.completed', response=dict(
                    id='new', model='fresh', status='completed')), conn=2))
                self.assertTrue(cap.flush())
                old = desktop_view(directory, thread_id=A)
                fresh = desktop_view(directory, thread_id=B)
                self.assertTrue(old['capture_lost'])
                self.assertIsNone(old['served'])
                self.assertEqual(old['verdict'], 'UNKNOWN')
                self.assertEqual(fresh['served'], 'fresh')
                self.assertEqual(fresh['verdict'], 'ok')
            finally:
                cap.close()

    def test_utf8_prompt_that_fits_memory_budget_is_not_charged_fourfold(self):
        with tempfile.TemporaryDirectory() as directory:
            cap = DesktopCapture(directory)
            try:
                request = dict(type='response.create', model='fixture',
                    client_metadata=dict(thread_id=A),
                    input=[dict(role='user', content='x' * (5 * 1024 * 1024) + '🐾')])
                message = proxy.WsMessage('c2s', json.dumps(request, ensure_ascii=False),
                    time.time(), 1)
                self.assertTrue(cap.feed(message))
                self.assertTrue(cap.flush())
                cap.feed(msg('s2c', dict(type='response.completed', response=dict(
                    id='large', model='fixture', status='completed'))))
                self.assertTrue(cap.flush())
                value = desktop_view(directory, thread_id=A)
                self.assertIsNone(value['observation_disabled'])
                self.assertEqual(value['served'], 'fixture')
                self.assertEqual(value['verdict'], 'ok')
                self.assertLess(Path(directory, 'desktop.json').stat().st_size, 8192)
            finally:
                cap.close()

    def test_failed_snapshot_write_can_recover_without_a_relay_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            cap = DesktopCapture(directory, publish_interval=.02)
            self.assertTrue(cap.flush())
            try:
                original = cap.publisher.publish
                failed = threading.Event()
                def failure(*args, **kwargs):
                    failed.set()
                    raise OSError('synthetic full disk')
                with patch.object(cap.publisher, 'publish', side_effect=failure):
                    cap.feed(msg('c2s', dict(type='response.create', model='fixture', client_metadata=dict(thread_id=A))))
                    self.assertTrue(failed.wait(1))
                    self.relay(cap.feed, cap.event)
                self.assertTrue(cap.flush())
                self.assertGreater(cap.publication_failures, 0)
                self.assertTrue(cap.worker.is_alive())
            finally:
                cap.close()

    def test_closed_connections_history_and_indexes_are_bounded(self):
        agg = live.LiveAggregator(max_rows=24, max_sessions=8, max_connections=4)
        for index in range(1000):
            tid = str(uuid.UUID(int=index % 8 + 1))
            conn = index + 1
            agg.connection_event('http_open', dict(conn=conn))
            agg.feed(proxy.WsMessage('c2s', json.dumps(dict(type='response.create', model='fixture',
                client_metadata=dict(thread_id=tid))), time.time(), conn, 'http'))
            agg.feed(proxy.WsMessage('s2c', json.dumps(dict(type='response.completed', response=dict(
                id=str(index), model='other' if index % 11 == 0 else 'fixture', status='completed'))),
                time.time(), conn, 'http'))
            agg.connection_event('http_close', dict(conn=conn))
        self.assertEqual(len(agg.conns), 0)
        self.assertEqual(len(agg.hints), 0)
        self.assertEqual(len(agg.open_connections), 0)
        self.assertEqual(len(agg.lost_connections), 0)
        self.assertLessEqual(len(agg.rows), 24)
        self.assertLessEqual(len(agg.rows_by_request), 32)
        self.assertEqual(len(agg.sessions()), 8)
        self.assertGreater(sum(x['historical_mismatches'] for x in agg.sessions()), 24)
        class NoHistoryTraversal(list):
            def __iter__(self):
                raise AssertionError('snapshot traversed the response history')
        agg.rows = NoHistoryTraversal(agg.rows)
        self.assertEqual(len(agg.sessions()), 8)

    def test_idle_and_capacity_eviction_mark_inflight_requests_unobserved(self):
        agg = live.LiveAggregator(max_connections=1, idle_seconds=30)
        agg.connection_event('ws_open', dict(conn=1))
        agg.feed(msg('c2s', dict(type='response.create', model='fixture', client_metadata=dict(thread_id=A))))
        agg.connection_event('ws_open', dict(conn=2))
        self.assertEqual(agg.current(A)['verdict'], 'UNKNOWN')
        self.assertFalse(agg.current(A)['connected'])
        agg.conns[2].touched -= 31
        agg.prune()
        self.assertFalse(agg.conns)

    def test_two_unacknowledged_requests_never_gain_false_fifo_model_matches(self):
        agg = live.LiveAggregator()
        for model in ('first', 'second'):
            agg.feed(msg('c2s', dict(type='response.create', model=model, client_metadata=dict(thread_id=A))))
        agg.feed(msg('s2c', dict(type='response.created', response=dict(id='second-only',model='second',status='in_progress'))))
        self.assertEqual(agg.current(A)['verdict'], 'UNKNOWN')
        self.assertIsNone(agg.current(A)['served'])
        self.assertTrue(agg.current(A)['capture_lost'])

    def test_html_429_is_observed_as_http_failure_and_wire_is_unchanged(self):
        source, writer = socket.socketpair()
        relay, reader = socket.socketpair()
        reader.settimeout(2)
        body = b'<html>upstream rate limited</html>'
        wire = b'HTTP/1.1 429 Too Many Requests\r\nContent-Type: text/html\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body
        values = []
        try:
            writer.sendall(wire)
            writer.shutdown(socket.SHUT_WR)
            forward_response(relay, source, 'POST', values.append)
            self.assertEqual(reader.recv(len(wire)), wire)
            self.assertEqual(values, [dict(type='error', error=dict(code='http_429',message='HTTP error response'))])
            self.assertNotIn('upstream rate limited', json.dumps(values))
            with tempfile.TemporaryDirectory() as directory:
                cap = DesktopCapture(directory)
                try:
                    cap.event('http_open', dict(conn=1))
                    cap.feed(proxy.WsMessage('c2s', json.dumps(dict(type='response.create',model='fixture',client_metadata=dict(thread_id=A))), time.time(), 1, 'http'))
                    cap.feed(proxy.WsMessage('s2c', json.dumps(values[0]), time.time(), 1, 'http'))
                    cap.event('http_close', dict(conn=1))
                    self.assertTrue(cap.flush())
                    value = desktop_view(directory, thread_id=A)
                    self.assertEqual(value['verdict'], 'ERROR')
                    self.assertEqual(value['label'], 'HTTP 응답 오류')
                    self.assertIsNone(value['served'])
                finally:
                    cap.close()
        finally:
            for sock in (source, writer, relay, reader):
                sock.close()

    def test_many_sse_lines_in_one_decoded_chunk_and_split_crlf(self):
        data = b'data: {"type":"fixture"}\r\n\r\n' * 20000
        counts = []
        Events(counts.append).feed(data)
        self.assertEqual(len(counts), 20000)
        split = []
        events = Events(split.append)
        for chunk in (b'data: {"type":"fixture"}\r', b'\n\r', b'\n'):
            events.feed(chunk)
        self.assertEqual(split, [dict(type='fixture')])

    def test_request_during_unfinished_response_does_not_misattribute_error(self):
        agg = live.LiveAggregator()
        agg.feed(msg('c2s', dict(type='response.create', model='first', client_metadata=dict(thread_id=A))))
        agg.feed(msg('s2c', dict(type='response.created', response=dict(id='first',model='first',status='in_progress'))))
        agg.feed(msg('c2s', dict(type='response.create', model='second', client_metadata=dict(thread_id=A))))
        agg.feed(msg('s2c', dict(type='error', error=dict(code='request_error',message='synthetic'))))
        self.assertEqual(agg.current(A)['verdict'], 'UNKNOWN')
        self.assertIsNone(agg.current(A)['served'])

    def test_long_lived_websocket_keeps_observing_after_history_eviction(self):
        agg = live.LiveAggregator(max_rows=4)
        agg.connection_event('ws_open', dict(conn=1))
        for index in range(30):
            agg.feed(msg('c2s', dict(type='response.create', model='fixture', client_metadata=dict(thread_id=A))))
            agg.feed(msg('s2c', dict(type='response.created', response=dict(id=str(index),model='fixture',status='in_progress'))))
            agg.feed(msg('s2c', dict(type='response.completed', response=dict(id=str(index),model='fixture',status='completed'))))
            self.assertEqual(agg.current(A)['verdict'], 'ok')
        self.assertLessEqual(len(agg.rows), 4)
        self.assertLessEqual(len(agg.conns[1].collector.by_id), 4)
        self.assertLessEqual(len(agg.conns[1].rows_by_id), 4)

    def test_new_request_on_an_idle_pooled_socket_restores_observation(self):
        agg = live.LiveAggregator(idle_seconds=30)
        agg.connection_event('ws_open', dict(conn=1))
        agg.conns[1].touched -= 31
        agg.prune()
        agg.feed(msg('c2s', dict(type='response.create', model='fixture', client_metadata=dict(thread_id=A))))
        agg.feed(msg('s2c', dict(type='response.created', response=dict(id='fresh',model='fixture',status='in_progress'))))
        self.assertTrue(agg.current(A)['connected'])
        self.assertFalse(agg.current(A)['capture_lost'])
        self.assertEqual(agg.current(A)['served'], 'fixture')

    def test_unbounded_variations_inside_one_response_disable_only_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            cap = DesktopCapture(directory)
            try:
                cap.feed(msg('c2s', dict(type='response.create',model='fixture',client_metadata=dict(thread_id=A))))
                for index in range(40):
                    cap.feed(msg('s2c', dict(type='response.created',response=dict(id='one',model=f'variant-{index}',status='in_progress'))))
                self.assertTrue(cap.flush())
                value = desktop_view(directory,thread_id=A)
                self.assertEqual(value['verdict'], 'UNKNOWN')
                self.assertIsNone(value['served'])
                self.assertEqual(value['observation_disabled'], 'observation_processing_failed')
                self.assertTrue(cap.worker.is_alive())
                cap.feed(msg('c2s', dict(type='response.create', model='fresh',
                    client_metadata=dict(thread_id=A)), conn=2))
                cap.feed(msg('s2c', dict(type='response.completed', response=dict(
                    id='recovered', model='fresh', status='completed')), conn=2))
                self.assertTrue(cap.flush())
                value = desktop_view(directory, thread_id=A)
                self.assertIsNone(value['observation_disabled'])
                self.assertEqual(value['served'], 'fresh')
                self.assertEqual(value['verdict'], 'ok')
            finally:
                cap.close()


if __name__ == '__main__':
    unittest.main()
