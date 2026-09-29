import unittest

from fluff_monitor.presentation import cache_breakdown, observation_status, progress_status


class PresentationEvidenceTests(unittest.TestCase):
    def test_running_work_and_matching_model_remain_independent(self):
        value = dict(activity="running", requested="gpt-6-astra", served="gpt-6-astra", verdict="OK")
        self.assertEqual(progress_status("gpt", value)[0], "작업 중")
        self.assertEqual(observation_status("gpt", value)[0], "모델 일치")

    def test_capture_loss_does_not_report_that_the_work_stopped(self):
        value = dict(activity="running", capture_lost=True, requested="gpt-6-astra", served=None)
        self.assertEqual(progress_status("gpt", value)[0], "작업 중")
        self.assertEqual(observation_status("gpt", value)[0], "관측 중단")

    def test_configured_model_is_not_evidence_of_a_response_or_match(self):
        for provider, value in (("gpt", dict(configured_model="gpt-6-astra", verdict="OK")),
                                ("claude", dict(requested_model="claude-opus-5-5"))):
            label, _, _ = observation_status(provider, value)
            self.assertNotIn("일치", label)
            self.assertNotIn("확인", label)
        label, _, _ = observation_status("gpt", dict(configured_model="gpt-6-astra", served="gpt-6-astra", verdict="OK"))
        self.assertEqual(label, "관측됨")

    def test_claude_start_setting_is_not_a_captured_request(self):
        label, _, detail = observation_status("claude", dict(requested_model="claude-opus-5-5", served="claude-opus-5-5"))
        self.assertEqual(label, "기록 확인")
        self.assertIn("시작 설정", detail)
        self.assertNotIn("일치", detail)

    def test_response_completion_does_not_prove_native_task_completion(self):
        value = dict(thread_id="x", active=True, connected=True, status="completed", metrics_available=False, activity="running")
        self.assertEqual(progress_status("gpt", value)[0], "최근 응답")
        value.update(active=False, connected=False)
        self.assertEqual(progress_status("gpt", value)[0], "상태 미확인")

    def test_cache_bar_excludes_output_and_does_not_treat_writes_as_hits(self):
        data = cache_breakdown(dict(last=dict(input_tokens=100, cached_input_tokens=40,
                                   cache_write_input_tokens=50, output_tokens=10000)))
        self.assertEqual((data["cached"], data["other"], data["fraction"]), (40, 60, .4))
        self.assertEqual(data["cached"]+data["other"], data["total"])

    def test_missing_or_inconsistent_counts_do_not_become_zero_percent(self):
        for total, cached in ((None, None), (0, 0), (10, None), (10, 11), (10, -1), (True, 1)):
            data = cache_breakdown(dict(last=dict(input_tokens=total, cached_input_tokens=cached)))
            self.assertFalse(data["known"])
            self.assertNotIn("percent", data)

    def test_real_zero_and_full_cache_are_valid(self):
        for cached, percent in ((0, "0.0%"), (100, "100.0%")):
            data = cache_breakdown(dict(last=dict(input_tokens=100, cached_input_tokens=cached)))
            self.assertTrue(data["known"])
            self.assertEqual(data["percent"], percent)


if __name__ == "__main__":
    unittest.main()
