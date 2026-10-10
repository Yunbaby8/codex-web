"""Bounded UTF-8 file access relative to an already validated workspace.

Linux directory descriptors and O_NOFOLLOW prevent symlink traversal, including
when another process replaces a directory between validation and opening it.
"""
import errno
import fcntl
import hashlib
import os
import stat
import time
from contextlib import contextmanager

from fastapi import HTTPException

MAX_EDITOR_BYTES = 2 * 1024 * 1024
DEFAULT_IGNORED = ['.git', 'build', 'tmp', 'node_modules', '__pycache__', '.venv']


class EditorFiles:
    def __init__(self, workspaces, ignored=None):
        self.workspaces = workspaces
        self.ignored = set(DEFAULT_IGNORED if ignored is None else ignored)

    @contextmanager
    def opened(self, workspace, path, *, directory=False, writable=False):
        root = self.workspaces.resolve(workspace)
        if path.startswith('/') or '\x00' in path or any(p in ('..', '.') for p in path.split('/')):
            raise HTTPException(403, '仅允许工作区内的相对路径')
        parts = path.split('/') if path else []
        if not directory and not parts:
            raise HTTPException(400, '请选择文件')
        if any(not p for p in parts):
            raise HTTPException(400, '文件路径无效')
        fd = None
        try:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for index, part in enumerate(parts):
                is_dir = index < len(parts) - 1 or directory
                flags = os.O_RDONLY if is_dir or not writable else os.O_RDWR
                flags |= os.O_NOFOLLOW | os.O_NONBLOCK
                if is_dir:
                    flags |= os.O_DIRECTORY
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            mode = os.fstat(fd).st_mode
            if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
                raise HTTPException(415, '仅支持普通文本文件和目录')
            yield fd
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise HTTPException(404, '文件或目录已不存在') from exc
            if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM):
                raise HTTPException(403, '无法访问：权限不足或路径包含符号链接') from exc
            raise HTTPException(400, '文件操作失败：' + str(exc)) from exc
        finally:
            if fd is not None:
                os.close(fd)

    def tree(self, workspace, path=''):
        entries = []
        with self.opened(workspace, path, directory=True) as fd:
            with os.scandir(fd) as children:
                for count, child in enumerate(children):
                    if count >= 10000:
                        raise HTTPException(413, '目录条目超过 10000，请选择更小的工作区或子目录')
                    if child.name in self.ignored or child.is_symlink():
                        continue
                    is_dir = child.is_dir(follow_symlinks=False)
                    if is_dir or child.is_file(follow_symlinks=False):
                        entries.append(dict(name=child.name, path='/'.join(filter(None, [path, child.name])), directory=is_dir))
        entries.sort(key=lambda item: (not item['directory'], item['name'].lower()))
        return dict(entries=entries, ignored=sorted(self.ignored))

    def file_index(self, workspace, *, limit=20000, scan_limit=100000, seconds=2):
        """Bounded filename-only scan; every directory uses the same no-symlink boundary."""
        paths, pending = [], ['']
        visited, skipped = 0, 0
        deadline = time.monotonic() + seconds
        truncated = False
        while pending:
            path = pending.pop()
            if time.monotonic() >= deadline:
                truncated = True
                break
            try:
                with self.opened(workspace, path, directory=True) as fd:
                    with os.scandir(fd) as children:
                        for child in children:
                            visited += 1
                            if visited > scan_limit or time.monotonic() >= deadline:
                                truncated = True
                                break
                            if child.name in self.ignored or child.is_symlink():
                                continue
                            relative = '/'.join(filter(None, [path, child.name]))
                            if child.is_dir(follow_symlinks=False):
                                if relative.count('/') < 40:
                                    pending.append(relative)
                                else:
                                    truncated = True
                            elif child.is_file(follow_symlinks=False):
                                paths.append(relative)
                                if len(paths) >= limit:
                                    truncated = True
                                    break
            except (HTTPException, OSError):
                if not path:
                    raise
                skipped += 1
            if len(paths) >= limit or visited > scan_limit:
                break
        return dict(paths=sorted(paths), truncated=truncated, skipped=skipped)

    @staticmethod
    def snapshot(fd):
        before = os.fstat(fd)
        if before.st_size > MAX_EDITOR_BYTES:
            raise HTTPException(413, '文件超过 2 MiB，无法在编辑器中打开')
        raw = os.pread(fd, MAX_EDITOR_BYTES + 1, 0)
        after = os.fstat(fd)
        if (before.st_mtime_ns, before.st_ctime_ns, before.st_size) != (after.st_mtime_ns, after.st_ctime_ns, after.st_size):
            raise HTTPException(409, '文件正在被其他程序修改，请重试')
        if len(raw) > MAX_EDITOR_BYTES:
            raise HTTPException(413, '文件超过 2 MiB，无法在编辑器中打开')
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise HTTPException(415, '二进制文件或非 UTF-8 文件，暂不支持编辑') from exc
        if any(ord(c) < 32 and c not in '\t\r\n\f' for c in content):
            raise HTTPException(415, '二进制文件，暂不支持编辑')
        revision = hashlib.sha256(raw).hexdigest() + f':{after.st_dev}:{after.st_ino}:{after.st_mtime_ns}:{after.st_ctime_ns}'
        return dict(content=content, revision=revision)

    def read(self, workspace, path):
        with self.opened(workspace, path) as fd:
            return dict(path=path, **self.snapshot(fd))

    def save(self, workspace, path, content, revision):
        raw = content.encode('utf-8')
        if len(raw) > MAX_EDITOR_BYTES:
            raise HTTPException(413, '保存内容超过 2 MiB')
        if any(ord(c) < 32 and c not in '\t\r\n\f' for c in content):
            raise HTTPException(415, '不能保存二进制内容')
        with self.opened(workspace, path, writable=True) as fd:
            # Serializes this server's saves. External editors need not obey flock;
            # revision checking detects changes already present before this write.
            fcntl.flock(fd, fcntl.LOCK_EX)
            current = self.snapshot(fd)
            if current['revision'] != revision:
                raise HTTPException(409, '磁盘文件已被其他程序修改，未覆盖。请先复制当前编辑内容，再重新加载文件。')
            offset = 0
            while offset < len(raw):
                offset += os.pwrite(fd, raw[offset:], offset)
            os.ftruncate(fd, len(raw))
            os.fsync(fd)
            saved = self.snapshot(fd)
            return dict(path=path, revision=saved['revision'])
