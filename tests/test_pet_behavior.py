import random
import os
import tempfile
import unittest
from pathlib import Path

from fluff_monitor.pet_state import MismatchAlerts, MotionCycle


def snapshot(*sessions, now=100, **extra):
    return dict(active=True,updated_at=now,publisher_pid=123,sessions=list(sessions),**extra)


def session(tid="a", count=0, bad=False, now=100):
    return dict(thread_id=tid,title="작업 "+tid,historical_mismatches=count,
                requested="gpt-6-astra",effort="max",served="gpt-6-sol" if bad else "gpt-6-astra",
                verdict="REROUTED" if bad else "ok",response_id=tid+str(count),observed_at=now)


class AlertsTests(unittest.TestCase):
    def test_background_mismatch_only_once_across_pet_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"alerts.json"; alerts=MismatchAlerts(path)
            self.assertEqual(alerts.poll(snapshot(session(),session("b")),100),[])
            view=snapshot(session(),session("b",1,True),now=101,thread_id="a")
            notices=alerts.poll(view,101)
            self.assertEqual(len(notices),1)
            self.assertEqual(notices[0]["thread_id"],"b")
            self.assertEqual(notices[0]["served"],"gpt-6-sol")
            self.assertEqual(alerts.poll(view,102),[])
            self.assertEqual(MismatchAlerts(path).poll(view,103),[])
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
            # Windows confidentiality is checked with the native DACL test.

    def test_fast_mismatch_followed_by_success_reports_count_without_false_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            alerts=MismatchAlerts(Path(temp)/"alerts.json")
            alerts.poll(snapshot(session()),100)
            notices=alerts.poll(snapshot(session(count=2,now=101),now=101),101)
            self.assertEqual(notices[0]["count"],2)
            self.assertFalse(notices[0]["detailed"])
            self.assertNotIn("served",notices[0])

    def test_late_task_registration_preserves_the_actual_mismatched_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            alerts=MismatchAlerts(Path(temp)/"alerts.json")
            alerts.poll(snapshot(),100)
            bad=session("b",1,True,101)
            self.assertEqual(alerts.poll(snapshot(now=101,observations=[bad]),101),[])
            good=session("b",1,False,102)
            notices=alerts.poll(snapshot(good,now=102),102)
            self.assertEqual(notices[0]["served"],"gpt-6-sol")
            self.assertEqual(notices[0]["title"],"작업 b")
            self.assertEqual(alerts.poll(snapshot(good,now=103),103),[])

    def test_stale_lost_and_unregistered_requests_do_not_alert(self):
        with tempfile.TemporaryDirectory() as temp:
            alerts=MismatchAlerts(Path(temp)/"alerts.json")
            alerts.poll(snapshot(session()),100)
            bad=session(count=1,bad=True)
            self.assertEqual(alerts.poll(snapshot(bad),110),[])
            lost=dict(bad,capture_lost=True)
            self.assertEqual(alerts.poll(snapshot(lost,now=111),111),[])
            ghost=session("ghost",1,True,112)
            self.assertEqual(alerts.poll(snapshot(now=112,observations=[ghost]),112),[])
            self.assertEqual(alerts.poll(snapshot(now=150,observations=[ghost]),150),[])
            self.assertEqual(alerts.pending,{})

    def test_old_history_is_not_replayed_when_first_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            alerts=MismatchAlerts(Path(temp)/"alerts.json")
            self.assertEqual(alerts.poll(snapshot(session(count=8,bad=True,now=20)),100),[])


class MotionTests(unittest.TestCase):
    def cycle(self):
        return MotionCycle(dict(idle=1,waiting=.75,review=.75,running=.5,failed=1,
                                waving=.8,jumping=.6,**{"running-left":1,"running-right":1}),
                           random.Random(3))

    def test_all_poses_appear_once_per_round_without_boundary_repeat(self):
        cycle=self.cycle(); sequence=[]; now=1
        for _ in range(27):
            pose,rest=cycle.sample(now)
            self.assertFalse(rest)
            sequence.append(pose)
            self.assertGreaterEqual(cycle.deadline-now,2.4)
            now=cycle.deadline
            self.assertEqual(cycle.sample(now),("idle",True))
            self.assertLessEqual(cycle.deadline-now,1)
            now=cycle.deadline
        for start in (0,9,18):
            self.assertEqual(set(sequence[start:start+9]),set(cycle.durations))
        self.assertNotEqual(sequence[8],sequence[9])
        self.assertNotEqual(sequence[17],sequence[18])

    def test_gestures_finish_complete_loops_at_the_slower_rate(self):
        cycle=self.cycle(); now=1
        for _ in range(18):
            pose,rest=cycle.sample(now)
            loops=(cycle.deadline-now)/cycle.durations[pose]
            self.assertAlmostEqual(loops,round(loops))
            now=cycle.deadline
            cycle.sample(now)
            now=cycle.deadline


if __name__ == "__main__":
    unittest.main()
