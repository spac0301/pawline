import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fluff_monitor.catalog import SnapshotCatalog
from fluff_monitor.storage import SnapshotReader, atomic_json
from fluff_monitor.views import desktop_view
from fluff_monitor.presentation import gpt_request, route_status


class SharedSnapshotsTests(unittest.TestCase):
    def test_native_tasks_and_usage_survive_response_capture_stopping(self):
        usage = dict(observed_at=99, last=dict(input_tokens=100, cached_input_tokens=90,
                                              cached_fraction=.9, output_tokens=10))
        activity = dict(updated_at=100, catalog_available=True, threads={
            'task': dict(title='현재 작업', kind='task', available=True, state='running',
                         state_at=99, last_model_activity_at=99, model='configured', effort='max', usage=usage),
            'child': dict(title='하위 작업', kind='subagent', parent_thread_id='task', state='running', state_at=99),
            'internal': dict(title='제목 생성', kind='internal', state='running', state_at=99)})
        catalog = SnapshotCatalog(); catalog.update(activity, now=100)
        for capture in ({}, dict(active=True, updated_at=100, sessions=[],
                                 observation_disabled='observation_queue_limit')):
            for selected in (None, 'task'):
                value = desktop_view(now=100, thread_id=selected, catalog=catalog,
                                     activity=activity, capture=capture)
                self.assertEqual([s['thread_id'] for s in value['sessions']], ['task'])
                self.assertEqual(value['thread_id'], 'task')
                self.assertEqual(value['usage'], usage)
                self.assertTrue(value['metrics_available'])
                self.assertIsNone(value['requested'])
                self.assertIsNone(value['served'])
                self.assertEqual(value['observations'], [])
                self.assertEqual(gpt_request(value)[:3], ('설정', 'configured', 'max'))
                self.assertNotIn(value['verdict'], ('ok', 'OK', 'REROUTED'))
                if capture:
                    self.assertEqual(route_status(value)[0], '관측 중단')
            missing = desktop_view(now=100, thread_id='missing', catalog=catalog,
                                   activity=activity, capture=capture)
            self.assertIsNone(missing.get('usage'))
            self.assertIsNone(missing.get('configured_model'))
        catalog.update(activity, now=111)
        stale = desktop_view(now=111, thread_id='task', catalog=catalog, activity=activity, capture={})
        self.assertFalse(stale['metrics_available'])
        self.assertEqual(stale['sessions'], [])
        self.assertIsNone(stale.get('usage'))

    def test_native_setting_never_overwrites_an_observed_request_or_response(self):
        activity = dict(updated_at=100, catalog_available=True, threads={
            'task': dict(title='현재 작업', kind='task', state='running', state_at=99,
                         model='configured', effort='max', usage=dict(observed_at=99))})
        capture = dict(active=True, updated_at=100, sessions=[dict(thread_id='task',
                       connected=True, requested='observed', served='observed', effort='low',
                       verdict='ok', response_id='r', observed_at=99)])
        catalog = SnapshotCatalog(); catalog.update(activity, now=100)
        value = desktop_view(now=100, catalog=catalog, activity=activity, capture=capture)
        self.assertEqual(gpt_request(value)[:3], ('요청', 'observed', 'low'))
        self.assertEqual(value['served'], 'observed')
        self.assertEqual(value['verdict'], 'ok')

    def test_refresh_uses_one_shared_catalog_and_marks_new_unobserved_work(self):
        activity=dict(updated_at=100,catalog_available=True,threads={
            'task':dict(title='현재 작업',kind='task',state='running',state_at=99,
                        usage=dict(observed_at=99))})
        capture=dict(active=True,updated_at=100,sessions=[dict(thread_id='task',connected=False,
                     observed_at=90,requested='gpt-6-astra',verdict='PENDING')])
        catalog=SnapshotCatalog()
        with patch('sqlite3.connect',side_effect=AssertionError('UI must use shared collector')):
            catalog.update(activity,now=100)
            value=desktop_view(now=100,catalog=catalog,activity=activity,capture=capture)
            self.assertEqual(value['title'],'현재 작업')
            self.assertEqual(value['verdict'],'OBSERVATION_GAP')
            self.assertIsNone(value['served'])
            activity['threads']['task']['state']='completed'
            completed=desktop_view(now=100,catalog=catalog,activity=activity,capture=capture)
            self.assertEqual(completed['verdict'],'OBSERVATION_GAP')
            catalog.update(activity,now=111)
            self.assertFalse(catalog.available)
            self.assertEqual(catalog.resolve(['task']),{})

    def test_unchanged_snapshots_are_reused_atomic_replacement_and_deletion_are_observed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';reader=SnapshotReader()
            atomic_json(path,dict(value=1))
            first=reader.read(path)
            with patch('fluff_monitor.storage.read_json',side_effect=AssertionError('Unchanged data decoded twice')):
                self.assertIs(reader.read(path),first)
            atomic_json(path,dict(value=2))
            self.assertEqual(reader.read(path),dict(value=2))
            path.unlink()
            self.assertEqual(reader.read(path),{})
