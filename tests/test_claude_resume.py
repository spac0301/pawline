"""Native session registration binds a resumed CLI without guessing by recency."""
import json
import sys
from pathlib import Path
import tempfile
import unittest

from fluff_monitor.activity import claude_processes, ClaudeCollector
from fluff_monitor.views import claude_view

A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'


@unittest.skipUnless(sys.platform == 'linux', 'Linux /proc and PID-namespace fixtures; Windows identity has separate tests')
class ClaudeResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = self.root/'proc'
        self.sessions = self.root/'sessions'
        self.proc.mkdir();self.sessions.mkdir()
        self.machine_id = self.root/'machine-id'
        self.machine_id.write_text('test-host')
        (self.proc/'stat').write_text('btime 1000\n')

    def process(self, pid=42, args=None, name='claude', start='4567', state='S'):
        p = self.proc/str(pid)
        p.mkdir(exist_ok=True)
        (p/'comm').write_text(name+'\n')
        (p/'cmdline').write_bytes(b'\0'.join(a.encode() for a in (args or ['claude']))+b'\0')
        fields = [state]+['0']*49
        fields[19] = start
        (p/'stat').write_text(f'{pid} ({name}) '+' '.join(fields))
        (p/'ns').mkdir(exist_ok=True)
        (p/'ns/pid').symlink_to('pid:[123]')
        return p

    def registration(self, tid=A, pid=42, start='4567', **changes):
        value = dict(pid=pid,sessionId=tid,procStart=start,entrypoint='cli',kind='interactive',
                     pidDomain='linux:test-host:pid:[123]',startedAt=2000000)
        value.update(changes)
        (self.sessions/f'{pid}.json').write_text(json.dumps(value))

    def collect(self):
        return claude_processes(self.proc,self.sessions,self.machine_id)

    def test_resumed_cli_without_session_argument_is_identified(self):
        self.process()
        self.registration()
        actual = self.collect()
        self.assertEqual(set(actual),{A})
        self.assertEqual(actual[A]['identity_source'],'native_session_registration')
        self.assertIsNone(actual[A]['requested_model'])
        self.assertEqual(actual[A]['start_ticks'],'4567')

    def test_resume_switch_does_not_keep_original_argv_session_or_settings(self):
        self.process(args=['claude','--session-id',A,'--model','old-model','--effort','low'])
        self.registration(tid=B)
        actual = self.collect()
        self.assertEqual(set(actual),{B})
        self.assertIsNone(actual[B]['requested_model'])
        self.assertIsNone(actual[B]['requested_effort'])

    def test_stale_pid_boot_host_or_namespace_registration_is_rejected(self):
        self.process()
        for changes in [dict(procStart='4566'),dict(pidDomain='linux:other-host:pid:[123]'),
                        dict(pidDomain='linux:test-host:pid:[999]'),dict(startedAt=999000),dict(sessionId='not-uuid')]:
            with self.subTest(changes=changes):
                self.registration(**changes)
                self.assertEqual(self.collect(),{})

    def test_exited_process_never_becomes_live_from_registration(self):
        self.registration()
        self.assertEqual(self.collect(),{})
        self.process(state='Z')
        self.assertEqual(self.collect(),{})

    def test_equal_form_and_npm_cli_arguments_are_supported(self):
        self.process(name='node',args=['node','/tools/node_modules/@anthropic-ai/claude-code/cli.js',
            '--resume='+A,'--model=claude-opus-5-5','--effort=max'])
        actual = self.collect()
        self.assertEqual(set(actual),{A})
        self.assertEqual(actual[A]['requested_model'],'claude-opus-5-5')
        self.assertEqual(actual[A]['requested_effort'],'max')

    def test_live_resumed_role_keeps_its_own_response_usage(self):
        self.process();self.registration()
        process = self.collect()[A]
        usage = dict(last=dict(input_tokens=100,cached_input_tokens=80,cached_fraction=.8),observed_at=100)
        activity = dict(updated_at=101,claude_roles=dict(implementation=dict(session_id=A,title='구현 담당')),
            claude_sessions={A:dict(process,session_id=A,process_alive=True,state='completed',state_at=100,
                                   served='claude-opus-5-5',usage=usage)})
        value = claude_view(activity,now=101)
        self.assertEqual(value['state'],'completed')
        self.assertEqual(value['served'],'claude-opus-5-5')
        self.assertEqual(value['usage'],usage)
        self.assertIsNone(value['requested_model'])


if __name__=='__main__':
    unittest.main()
