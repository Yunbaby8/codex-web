import os
import tempfile
import unittest
from windows_fixture import junction
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from backend.editor_files import EditorFiles, MAX_EDITOR_BYTES
from backend.workspace import Workspaces


class EditorFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        self.files = EditorFiles(Workspaces([self.base]))
        self.path = self.root / 'hello.cpp'
        self.path.write_bytes(b'\xef\xbb\xbfint main() {}\r\n')

    def tearDown(self):
        self.temp.cleanup()

    def assert_status(self, status, callback, *args):
        with self.assertRaises(HTTPException) as error:
            callback(*args)
        self.assertEqual(error.exception.status_code, status)

    def test_file_index_ignores_large_dirs_and_symlink_escapes(self):
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'device.cpp').write_text('source')
        for name in ('.git', 'node_modules', 'build', 'tmp'):
            (self.root / name).mkdir()
            (self.root / name / 'hidden.cpp').write_text('ignored')
        outside = self.base / 'outside';outside.mkdir()
        (outside / 'secret.cpp').write_text('must not be listed')
        junction(self.root / 'escape', outside)
        result = self.files.file_index(self.root)
        self.assertEqual(result['paths'], ['hello.cpp', 'src/device.cpp'])
        self.assertFalse(result['truncated'])
        limited = self.files.file_index(self.root, limit=1)
        self.assertEqual(len(limited['paths']), 1)
        self.assertTrue(limited['truncated'])
        with self.assertRaises(ValueError):
            self.files.file_index(self.base.parent)

    def test_roundtrip_preserves_bom_line_endings_permissions(self):
        permissions = self.path.stat().st_mode
        original = self.files.read(self.root, 'hello.cpp')
        content = original['content'].replace('{}', '{return 0;}')
        saved = self.files.save(self.root, 'hello.cpp', content, original['revision'])
        self.assertEqual(self.path.read_bytes(), content.encode())
        self.assertEqual(self.path.stat().st_mode, permissions)
        self.assertEqual(saved['revision'], self.files.read(self.root, 'hello.cpp')['revision'])

    def test_external_change_and_replacement_conflict(self):
        for replace in (False, True):
            original = self.files.read(self.root, 'hello.cpp')
            if replace:
                replacement = self.root / 'replacement'
                replacement.write_text('external replacement')
                replacement.replace(self.path)
            else:
                self.path.write_text('external edit')
            expected = self.path.read_bytes()
            self.assert_status(409, self.files.save, self.root, 'hello.cpp', 'ours', original['revision'])
            self.assertEqual(self.path.read_bytes(), expected)

    def test_parallel_saves_only_one_wins(self):
        revision = self.files.read(self.root, 'hello.cpp')['revision']
        def save(content):
            try:
                self.files.save(self.root, 'hello.cpp', content, revision)
                return 200
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(save, ['first', 'second'])), [200, 409])

    def test_traversal_absolute_symlinks_and_special_files_denied(self):
        outside = self.base / 'outside'
        outside.write_text('private')
        junction(self.root / 'dirlink', self.base)
        denied = ['../outside', str(outside), 'dirlink/outside']
        if os.name == 'nt':
            denied += ['C:outside', 'dirlink\\outside', 'hello.cpp:stream', 'CON', 'hello.cpp.']
        for path in denied:
            self.assert_status(403, self.files.read, self.root, path)
            self.assert_status(403, self.files.save, self.root, path, 'bad', 'revision')
        self.assertEqual(outside.read_text(), 'private')
        self.assert_status(404, self.files.read, self.root, 'missing')

    def test_binary_encoding_and_size_limits(self):
        for data in (b'\x00hello', b'\xff\xfe', b'hello\x01'):
            self.path.write_bytes(data)
            self.assert_status(415, self.files.read, self.root, 'hello.cpp')
        self.path.write_bytes(b'a' * (MAX_EDITOR_BYTES + 1))
        self.assert_status(413, self.files.read, self.root, 'hello.cpp')
        self.assert_status(413, self.files.save, self.root, 'hello.cpp', '字' * MAX_EDITOR_BYTES, 'revision')

    def test_tree_is_lazy_sorted_configurable_and_skips_symlinks(self):
        (self.root / 'src').mkdir()
        (self.root / 'build').mkdir()
        (self.root / '.git').mkdir()
        junction(self.root / 'link', self.base)
        result = self.files.tree(self.root)
        self.assertEqual([e['name'] for e in result['entries']], ['src', 'hello.cpp'])
        self.assertEqual(self.files.tree(self.root, 'src')['entries'], [])
        visible = EditorFiles(self.files.workspaces, []).tree(self.root)
        self.assertIn('build', [e['name'] for e in visible['entries']])


if __name__ == '__main__':
    unittest.main()
