import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from fluff_monitor.platform_support import lock_exclusive, windows_claude_processes
from fluff_monitor.storage import atomic_json


class PlatformTests(unittest.TestCase):
    def test_explicit_windows_cli_identity_ignores_unidentified_or_unrelated_processes(self):
        tid='11111111-1111-4111-8111-111111111111'
        values=[dict(pid=1,name='claude.exe',cmdline=['claude.exe','--resume',tid,'--model','claude-opus-5-5','--effort=max'],create_time=10),
                dict(pid=2,name='claude.exe',cmdline=['claude.exe'],create_time=11),
                dict(pid=3,name='unrelated.exe',cmdline=['unrelated.exe','--resume',tid],create_time=12)]
        result=windows_claude_processes([SimpleNamespace(info=v) for v in values])
        self.assertEqual(set(result),{tid})
        self.assertEqual(result[tid]['pid'],1)
        self.assertEqual(result[tid]['requested_effort'],'max')

    def test_lock_is_exclusive_across_processes(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'lock'
            code='from pathlib import Path;from fluff_monitor.platform_support import lock_exclusive;import sys;f=Path(sys.argv[1]).open("a+b");print(lock_exclusive(f))'
            with path.open('a+b') as first:
                self.assertTrue(lock_exclusive(first))
                result=subprocess.check_output([sys.executable,'-B','-c',code,str(path)],cwd=root,text=True)
                self.assertEqual(result.strip(),'False')
            result=subprocess.check_output([sys.executable,'-B','-c',code,str(path)],cwd=root,text=True)
            self.assertEqual(result.strip(),'True')

    @unittest.skipUnless(sys.platform == 'win32', 'Requires native Windows token and ACL APIs')
    def test_windows_snapshot_acl_allows_only_current_user(self):
        import win32api,win32security
        token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32security.TOKEN_QUERY)
        try:sid=win32security.GetTokenInformation(token,win32security.TokenUser)[0]
        finally:token.Close()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'private.json';atomic_json(path,dict(test=True))
            sd=win32security.GetNamedSecurityInfo(str(path),win32security.SE_FILE_OBJECT,win32security.DACL_SECURITY_INFORMATION)
            acl=sd.GetSecurityDescriptorDacl()
            self.assertEqual(acl.GetAceCount(),1)
            self.assertEqual(acl.GetAce(0)[2],sid)
