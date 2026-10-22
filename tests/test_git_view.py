import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from windows_fixture import junction
from pathlib import Path

from fastapi import HTTPException
from backend.editor_files import EditorFiles, MAX_EDITOR_BYTES
from backend.git_view import GitView
from backend.workspace import Workspaces


class GitViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'repo';self.root.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'core.autocrlf', 'false')
        self.path = self.root / 'file.txt';self.path.write_text('base\n', encoding="utf-8", newline="")
        self.git('add', '.');self.git('commit', '-m', 'fixture')
        self.view = GitView(EditorFiles(Workspaces([self.base])))

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.DEVNULL)

    def status_error(self, status, fn, *args):
        with self.assertRaises(HTTPException) as result:
            fn(*args)
        self.assertEqual(result.exception.status_code, status)

    @unittest.skipUnless(os.name == 'nt', 'Windows console creation flags')
    def test_status_subprocesses_do_not_create_console_windows(self):
        with patch('backend.git_view.subprocess.run', wraps=subprocess.run) as run:
            result = self.view.status(self.root)
        self.assertEqual(result['branch'], 'main')
        self.assertGreater(run.call_count, 0)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)

    def test_staged_and_unstaged_diff_are_distinct_and_read_only(self):
        self.path.write_text('staged\n', encoding="utf-8", newline="");self.git('add', '.')
        self.path.write_text('disk\n', encoding="utf-8", newline="")
        index = self.root / '.git/index'
        before = (index.read_bytes(), index.stat().st_mtime_ns, self.git('rev-parse', 'HEAD'))
        result = self.view.status(self.root)
        self.assertEqual(result['branch'], 'main')
        self.assertEqual([e['group'] for e in result['entries']], ['staged', 'unstaged'])
        staged = self.view.diff(self.root, 'file.txt', 'staged')
        unstaged = self.view.diff(self.root, 'file.txt', 'unstaged')
        self.assertEqual((staged['original'], staged['modified']), ('base\n', 'staged\n'))
        self.assertEqual((unstaged['original'], unstaged['modified']), ('staged\n', 'disk\n'))
        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns, self.git('rev-parse', 'HEAD')), before)
        self.assertEqual(self.path.read_text(), 'disk\n')

    def test_added_deleted_and_untracked_names_are_literal(self):
        for name in ['新 文件.txt', '-option.txt', '[literal].txt', 'space name.txt']:
            (self.root / name).write_text(name, encoding="utf-8", newline="")
            result = self.view.diff(self.root, name, 'untracked')
            self.assertEqual((result['original'], result['modified']), ('', name))
        self.path.unlink()
        self.assertEqual(self.view.diff(self.root, 'file.txt', 'unstaged')['modified'], '')
        self.git('add', '--all')
        self.assertEqual(self.view.diff(self.root, 'file.txt', 'staged')['modified'], '')
        self.assertEqual(self.view.diff(self.root, '新 文件.txt', 'staged')['original'], '')

    def test_rename_and_worktree(self):
        self.git('mv', 'file.txt', 'renamed.txt')
        result = self.view.diff(self.root, 'renamed.txt', 'staged')
        self.assertEqual(result['originalPath'], 'file.txt')
        self.assertEqual(result['original'], result['modified'])
        worktree = self.base / 'linked'
        self.git('worktree', 'add', '-b', 'linked', str(worktree))
        (worktree / 'file.txt').write_text('linked edit', encoding="utf-8", newline="")
        self.assertEqual(self.view.status(worktree)['branch'], 'linked')
        self.assertEqual(self.view.diff(worktree, 'file.txt', 'unstaged')['modified'], 'linked edit')

    def test_non_repository_unborn_and_detached_head(self):
        empty = self.base / 'empty';empty.mkdir()
        self.assertFalse(self.view.status(empty)['repository'])
        subprocess.check_call(['git', '-C', str(empty), 'init', '-q', '-b', 'new'])
        (empty / 'first').write_text('first', encoding="utf-8", newline="")
        subprocess.check_call(['git', '-C', str(empty), 'add', '.'])
        self.assertEqual(self.view.diff(empty, 'first', 'staged')['original'], '')
        self.assertEqual(self.view.status(empty)['branch'], 'new')
        self.git('checkout', '--detach')
        self.assertTrue(self.view.status(self.root)['detached'])

    def test_subdirectory_scope_and_invalid_paths(self):
        sub = self.root / 'sub';sub.mkdir()
        (sub / 'inside.txt').write_text('inside', encoding="utf-8", newline="")
        self.path.write_text('outside selected workspace', encoding="utf-8", newline="")
        self.assertEqual([e['path'] for e in self.view.status(sub)['entries']], ['inside.txt'])
        self.status_error(409, self.view.diff, sub, '../file.txt', 'unstaged')
        self.status_error(409, self.view.diff, self.root, str(self.path), 'unstaged')
        self.status_error(400, self.view.diff, self.root, 'file.txt', 'HEAD; echo bad')

    def test_binary_large_symlink_and_non_utf8(self):
        for data in [b'\x00binary', b'\xff\xfe', b'a' * (MAX_EDITOR_BYTES + 1)]:
            self.path.write_bytes(data)
            expected = 413 if len(data) > MAX_EDITOR_BYTES else 415
            self.status_error(expected, self.view.diff, self.root, 'file.txt', 'unstaged')
            self.git('add', '.')
            self.status_error(expected, self.view.diff, self.root, 'file.txt', 'staged')
            self.git('reset', '--', 'file.txt')
        target = self.base / 'outside';target.mkdir()
        (target/'private').write_text('private')
        junction(self.root/'link', target)
        self.status_error(403, self.view.files.read, self.root, 'link/private')
        # A staged symlink blob must still be rejected even without symlink privilege.
        oid = subprocess.check_output(['git','-C',str(self.root),'hash-object','-w','--stdin'], input=b'outside').decode().strip()
        self.git('update-index','--add','--cacheinfo','120000',oid,'staged-link')
        self.status_error(415, self.view.diff, self.root, 'staged-link', 'staged')

    def test_status_does_not_execute_clean_filters(self):
        marker = self.root / 'filter-ran'
        (self.root / '.gitattributes').write_text('*.txt filter=fixture\n', encoding="utf-8", newline="")
        self.git('config', 'filter.fixture.clean', 'touch filter-ran; cat')
        self.git('config', 'filter.fixture.required', 'true')
        self.path.write_text('dirty file\n', encoding="utf-8", newline="")
        result = self.view.status(self.root)
        self.assertTrue(result['entries'])
        self.assertFalse(marker.exists())

    def test_conflicts_are_reported_without_resolution(self):
        self.git('checkout', '-b', 'other')
        self.path.write_text('other\n', encoding="utf-8", newline="");self.git('commit', '-am', 'other fixture')
        self.git('checkout', 'main')
        self.path.write_text('main\n', encoding="utf-8", newline="");self.git('commit', '-am', 'main fixture')
        with self.assertRaises(subprocess.CalledProcessError):
            self.git('merge', 'other')
        self.assertEqual(self.view.status(self.root)['entries'][0]['group'], 'conflicts')
        self.status_error(409, self.view.diff, self.root, 'file.txt', 'conflicts')


if __name__ == '__main__':
    unittest.main()
