"""Snapshot IO and liveness contracts with an isolated clock and directory."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fluff_monitor.activity import ActivityCollector
from fluff_monitor.catalog import SnapshotCatalog
from fluff_monitor.storage import (Publisher, SnapshotReader, atomic_json,
                                   heartbeat_path, read_json, read_snapshot)
from fluff_monitor.views import claude_view, desktop_view


class SnapshotHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / 'activity.json'
        self.pulse = heartbeat_path(self.path)
        self.publisher = Publisher(self.directory)
        self.clock = 100.0
        self.wall = patch('fluff_monitor.storage.time.time', side_effect=lambda: self.clock)
        self.elapsed = patch('fluff_monitor.storage.time.monotonic', side_effect=lambda: self.clock)
        self.wall.start(); self.elapsed.start()
        self.addCleanup(self.wall.stop); self.addCleanup(self.elapsed.stop)
        self.data = dict(updated_at=100, catalog_available=True,
                         threads={'task': {'title': 'Task', 'state': 'completed'}},
                         claude_sessions={'c': dict(session_id='c', process_alive=True,
                             state='completed', state_at=90,
                             usage={'observed_at': 90, 'last': {'input_tokens': 100}})})

    def publish(self):
        self.publisher.publish('activity', self.data, heartbeat=True)

    def test_unchanged_content_stays_fresh_without_rewriting_or_decoding_data(self):
        self.publish()
        original = self.path.read_bytes()
        original_stat = self.path.stat()
        reader = SnapshotReader()
        reader.read(self.path)
        reads = []
        def read(path):
            reads.append(Path(path))
            return read_json(path)
        with patch('fluff_monitor.storage.read_json', side_effect=read):
            for tick in range(102, 132, 2):
                self.clock = tick
                self.data['updated_at'] = tick
                self.publish()
                value = reader.read(self.path)
                catalog = SnapshotCatalog(); catalog.update(value, now=tick)
                self.assertTrue(catalog.available)
                self.assertEqual(claude_view(value, now=tick)['state'], 'completed')
                self.assertEqual(value['claude_sessions']['c']['usage']['observed_at'], 90)
        self.assertNotIn(self.path, reads)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.path.stat().st_mtime_ns, original_stat.st_mtime_ns)
        self.assertEqual(self.path.stat().st_ino, original_stat.st_ino)
        self.assertLess(self.pulse.stat().st_size, 160)
        value = reader.read(self.path)
        catalog.update(value, now=141)
        self.assertFalse(catalog.available)
        self.assertEqual(claude_view(value, now=141)['state'], 'unknown')

    def test_mutating_nested_usage_publishes_new_content_and_snapshot_identity(self):
        self.publish()
        before = read_json(self.path)
        self.clock = 102
        self.data['claude_sessions']['c']['usage']['last']['input_tokens'] = 200
        self.publish()
        after = read_snapshot(self.path)
        self.assertNotEqual(after['snapshot_id'], before['snapshot_id'])
        self.assertEqual(after['claude_sessions']['c']['usage']['last']['input_tokens'], 200)
        self.assertEqual(after['snapshot_id'], read_json(self.pulse)['snapshot_id'])

    def test_restart_and_interleaved_old_heartbeat_cannot_refresh_another_snapshot(self):
        self.publish()
        first = read_json(self.path)
        old_pulse = read_json(self.pulse)
        self.clock = 120
        self.publisher = Publisher(self.directory)
        self.publish()
        latest = read_json(self.path)
        self.assertNotEqual(first['snapshot_id'], latest['snapshot_id'])
        old_pulse['updated_at'] = 200
        atomic_json(self.pulse, old_pulse)
        self.assertEqual(read_snapshot(self.path)['updated_at'], 120)
        atomic_json(self.path, first)
        atomic_json(self.pulse, dict(version=2, snapshot_id=latest['snapshot_id'], updated_at=200))
        self.assertEqual(SnapshotReader().read(self.path)['updated_at'], 100)

    def test_failed_content_write_does_not_renew_old_data_and_recovers(self):
        self.publish()
        before = self.pulse.read_bytes()
        self.clock = 120
        self.data['threads']['task']['state'] = 'running'
        def fail_data(path, value):
            if path == self.path:
                raise OSError('synthetic disk failure')
            atomic_json(path, value)
        with patch('fluff_monitor.storage.atomic_json', side_effect=fail_data):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(self.pulse.read_bytes(), before)
        catalog = SnapshotCatalog(); catalog.update(read_snapshot(self.path), now=120)
        self.assertFalse(catalog.available)
        self.publish()
        self.assertEqual(read_snapshot(self.path)['threads']['task']['state'], 'running')
        catalog.update(read_snapshot(self.path), now=120)
        self.assertTrue(catalog.available)

    def test_missing_or_bad_pulse_falls_back_and_missing_data_is_recreated(self):
        self.publish()
        for raw in ('not json', '{}', json.dumps(dict(version=2,
                    snapshot_id=read_json(self.path)['snapshot_id'], updated_at=float('nan')))):
            self.pulse.write_text(raw)
            self.assertEqual(read_snapshot(self.path)['updated_at'], 100)
        self.pulse.unlink()
        self.assertEqual(read_snapshot(self.path)['updated_at'], 100)
        self.path.unlink()
        self.assertEqual(read_snapshot(self.path), {})
        self.clock = 120
        self.publish()
        self.assertEqual(read_snapshot(self.path)['updated_at'], 120)

    def test_legacy_snapshot_ignores_sidecars_and_activity_uses_shared_publisher(self):
        atomic_json(self.path, dict(updated_at=50, threads={}))
        atomic_json(self.pulse, dict(version=2, snapshot_id='stray', updated_at=100))
        self.assertEqual(read_snapshot(self.path)['updated_at'], 50)
        collector = ActivityCollector(self.directory, self.directory/'unused.db')
        with patch.object(collector, 'snapshot', return_value=self.data):
            collector.publish()
        self.assertEqual(read_snapshot(self.path)['version'], 2)
        self.assertEqual(read_snapshot(self.path)['updated_at'], 100)

    def test_routing_view_uses_the_same_liveness_rule_and_expires_after_stop(self):
        route = dict(active=True, connected=True, response_id='response',
                     requested='model', served='model', verdict='ok')
        self.publisher.publish('desktop', route, heartbeat=True)
        path = self.directory / 'desktop.json'
        before = path.read_bytes()
        self.clock = 120
        self.publisher.publish('desktop', route, heartbeat=True)
        value = desktop_view(self.directory, now=120)
        self.assertTrue(value['active'])
        self.assertEqual(value['served'], 'model')
        self.assertEqual(path.read_bytes(), before)
        value = desktop_view(self.directory, now=128)
        self.assertFalse(value['active'])
        self.assertEqual(value['verdict'], 'UNKNOWN')

    def test_failed_heartbeat_write_expires_and_can_recover_without_rewriting_data(self):
        self.publish()
        before = self.path.read_bytes()
        self.clock = 120
        with patch('fluff_monitor.storage.atomic_json', side_effect=OSError('pulse write failure')):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(read_snapshot(self.path)['updated_at'], 100)
        self.publish()
        self.assertEqual(read_snapshot(self.path)['updated_at'], 120)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
