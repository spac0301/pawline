import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fluff_monitor.catalog import SnapshotCatalog
from fluff_monitor.storage import SnapshotReader, atomic_json
from fluff_monitor.views import desktop_view


class SharedSnapshotsTests(unittest.TestCase):
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
