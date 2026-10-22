"""Platform regressions, using temporary files and offline child processes only."""
import asyncio
import os
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from backend.codex_client import CodexClient
from backend.config import load_config
from backend.editor_files import EditorFiles
from backend.workspace import Workspaces


class PlatformConfigTests(unittest.TestCase):
    def test_default_binary_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'config.yaml'
            config.write_text('username: test\npassword: fixture\n', encoding='utf-8')
            for platform, expected in [('nt', 'codex.exe'), ('posix', 'codex')]:
                environment = dict(os.environ, CODEX_WEB_CONFIG=str(config))
                with patch('backend.config.os', SimpleNamespace(name=platform, environ=environment)):
                    self.assertEqual(load_config()['codex_binary'], expected)
            config.write_text('username: test\npassword: fixture\ncodex_binary: custom-cli\n', encoding='utf-8')
            with patch.dict(os.environ, CODEX_WEB_CONFIG=str(config)):
                self.assertEqual(load_config()['codex_binary'], 'custom-cli')


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux file descriptors and process groups')
class LinuxFileTests(unittest.TestCase):
    def test_symlink_fifo_and_case_sensitive_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'Name.txt').write_text('upper')
            (root / 'name.txt').write_text('lower')
            (root / 'link').symlink_to(root / 'Name.txt')
            os.mkfifo(root / 'pipe')
            files = EditorFiles(Workspaces([root]))
            self.assertEqual(files.read(root, 'Name.txt')['content'], 'upper')
            self.assertEqual(files.read(root, 'name.txt')['content'], 'lower')
            for name, status in [('link', 403), ('pipe', 415)]:
                with self.assertRaises(HTTPException) as error:
                    files.read(root, name)
                self.assertEqual(error.exception.status_code, status)
            self.assertEqual({e['name'] for e in files.tree(root)['entries']}, {'Name.txt', 'name.txt'})


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux process groups')
class LinuxProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_cleans_children_with_live_or_exited_parent(self):
        code = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid,flush=True); sys.stdin.readline()"
        for exited in (False, True):
            with self.subTest(parent_exited=exited):
                process = await asyncio.create_subprocess_exec(sys.executable, '-c', code,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, start_new_session=True)
                async def on_message(message):
                    pass
                client = CodexClient(sys.executable, on_message, lambda: None)
                client.proc = process
                try:
                    child = int(await asyncio.wait_for(process.stdout.readline(), 5))
                    if exited:
                        process.stdin.close()
                        await asyncio.wait_for(process.wait(), 5)
                    await client.close()
                    for _ in range(50):
                        status = Path(f'/proc/{child}/stat')
                        if not status.exists() or status.read_text().split(') ', 1)[1].split()[0] == 'Z':
                            break
                        await asyncio.sleep(.02)
                    else:
                        self.fail('Owned child survived closing the Linux client')
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
