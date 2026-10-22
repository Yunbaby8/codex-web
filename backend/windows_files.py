"""Windows file handles with final-path containment and no reparse traversal."""
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import msvcrt
import os
from pathlib import Path, PureWindowsPath
import stat

from fastapi import HTTPException

kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel.CreateFileW.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
INVALID_HANDLE = ctypes.c_void_p(-1).value


def is_reparse(path):
    return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


@contextmanager
def open_workspace_path(root, relative, *, directory=False, writable=False):
    # API file paths use '/'; reject Windows alternate streams and device names.
    parts = relative.split('/') if relative else []
    if (PureWindowsPath(relative).drive or relative.startswith(('/', '\\'))
            or any(not p or p in ('.', '..') or any(c in p for c in '\\:\x00<>"|?*')
                   or p.endswith((' ', '.')) or PureWindowsPath(p).is_reserved() for p in parts)):
        raise HTTPException(403, '仅允许工作区内的普通相对路径')
    if not directory and not parts:
        raise HTTPException(400, '请选择文件')
    handle, fd = None, None
    try:
        target = root
        for part in parts:
            target /= part
            if is_reparse(target):
                raise HTTPException(403, '不允许通过符号链接或目录联接访问文件')
        # OPEN_EXISTING, OPEN_REPARSE_POINT, BACKUP_SEMANTICS (directory handles).
        # An active save excludes other writers and replacement of the opened file.
        handle = kernel.CreateFileW(str(target), 0xC0000000 if writable else 0x80000000,
                                    1 if writable else (3 if directory else 7), None, 3, 0x02200000, None)
        if handle == INVALID_HANDLE:
            handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise ctypes.WinError(ctypes.get_last_error())
        value = buffer.value
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        final = Path(value)
        if final != root and root not in final.parents:
            raise HTTPException(403, '打开的文件不在当前工作区内')
        if is_reparse(target):
            raise HTTPException(403, '不允许访问链接')
        if directory:
            if not final.is_dir():
                raise HTTPException(403, '路径不是目录')
            yield final
        else:
            fd = msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if writable else os.O_RDONLY))
            handle = None  # fd now owns the handle.
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise HTTPException(415, '仅支持普通文件')
            yield fd
    except OSError as exc:
        code = 404 if isinstance(exc, FileNotFoundError) else 403
        raise HTTPException(code, '文件不存在或无法访问：' + str(exc)) from exc
    finally:
        if fd is not None:
            os.close(fd)
        if handle is not None:
            kernel.CloseHandle(handle)
