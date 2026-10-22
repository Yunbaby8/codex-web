"""Native Windows regressions for process ownership and chat file references."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

if os.name == 'nt':
    from backend.windows_process import ProcessJob


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object')
class WindowsProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_job_closes_child_after_parent_has_exited(self):
        # Use the real interpreter rather than the Windows venv launcher.
        code = "import subprocess,sys;sys.stdin.readline();p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(p.pid,flush=True)"
        parent = await asyncio.create_subprocess_exec(sys._base_executable, '-c', code,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)
        job = ProcessJob(parent.pid)
        try:
            parent.stdin.write(b'go\n');await parent.stdin.drain()
            child = int(await asyncio.wait_for(parent.stdout.readline(), 5))
            await asyncio.wait_for(parent.wait(), 5)
            job.close()
            # tasklist is read-only and avoids opening unrelated process handles.
            for _ in range(30):
                output = subprocess.check_output(['tasklist.exe','/FI',f'PID eq {child}','/FO','CSV','/NH'])
                if str(child).encode() not in output:
                    break
                await asyncio.sleep(0.1)
            else:
                self.fail('Windows Job did not stop its remaining child')
        finally:
            job.close()
            if parent.returncode is None:
                parent.kill();await parent.wait()


class WindowsReferenceTests(unittest.TestCase):
    def test_windows_file_links_and_case_insensitive_boundaries(self):
        text = (Path(__file__).resolve().parents[1]/'frontend/conversation.js').read_text(encoding='utf-8')
        start = text.index('  function workspaceFileReference(')
        end = text.index('  function localFileHref(',start)
        function = text[start:end]
        cases = [
            ('/home/eric/Project/src/a.cpp:3', '/home/eric/Project', 'src/a.cpp',3,1),
            ('/home/eric/project/src/a.cpp', '/home/eric/Project', None,None,None),
            ('/home/eric/Project-other/a.cpp', '/home/eric/Project', None,None,None),
            ('C:\\repo\\项目\\src\\main.cpp:12:3', 'C:/repo/项目', 'src/main.cpp',12,3),
            ('c:/REPO/项目/src/main.cpp#L8', 'C:/repo/项目', 'src/main.cpp',8,1),
            ('src/main.cpp:5', 'C:/repo/项目', 'src/main.cpp',5,1),
            ('C:\\main.cpp:1', 'C:/', 'main.cpp',1,1),
            ('C:/repo/项目外/main.cpp', 'C:/repo/项目', None,None,None),
            ('D:/repo/main.cpp', 'C:/repo', None,None,None),
            ('../outside.cpp', 'C:/repo', None,None,None),
            ('https://example.invalid/a.cpp', 'C:/repo', None,None,None),
        ]
        code = function + '\nconst cases='+json.dumps(cases)+';for(const [target,root,path,line,column] of cases){const r=workspaceFileReference(target,root);if(path===null){if(r!==null)throw Error(target);}else if(!r||r.path!==path||r.line!==line||r.column!==column)throw Error(JSON.stringify({target,r}));}'
        subprocess.run(['node','-e',code],check=True,capture_output=True)


if __name__ == '__main__':
    unittest.main()
