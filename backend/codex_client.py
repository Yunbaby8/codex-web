"""Persistent stdio RPC transport. No workspace, session or database policy."""
import asyncio
import contextlib
import json
import logging
import subprocess
import os
import signal
if os.name == 'nt':
    from .windows_process import ProcessJob

log = logging.getLogger(__name__)


async def _read_lines(stream):
    # Read chunks rather than StreamReader.readline(): thread snapshots can exceed
    # the stream's buffer limit. Preserve framing across chunks, including UTF-8.
    buffered = bytearray()
    while chunk := await stream.read(64 * 1024):
        buffered.extend(chunk)
        if b'\n' not in chunk:
            continue
        lines = buffered.split(b'\n')
        buffered = lines.pop()
        for line in lines:
            yield line
    if buffered:
        yield buffered


class CodexClient:
    def __init__(self, binary, on_message, on_disconnect):
        self.binary = binary
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self.proc = None
        self.job = None
        self.tasks = []
        self.pending = {}
        self.counter = 0
        self.write_lock = asyncio.Lock()

    async def start(self):
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        self.proc = await asyncio.create_subprocess_exec(
            self.binary, 'app-server', '--listen', 'stdio://',
            # The Web question dialog also needs the tool available in Default mode.
            '--enable', 'default_mode_request_user_input',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=8*1024*1024, **options)
        try:
            if os.name == 'nt':
                self.job = ProcessJob(self.proc.pid)
        except BaseException:
            self.proc.kill()
            await self.proc.wait()
            raise
        self.tasks = [asyncio.create_task(self.read()), asyncio.create_task(self.stderr())]
        await self.rpc('initialize', {
            'clientInfo': {'name':'codex_web','version':'0.1.0'},
            'capabilities': {'experimentalApi':True},
        })
        await self.write({'method':'initialized'})

    async def write(self, message):
        async with self.write_lock:
            if not self.proc or self.proc.returncode is not None:
                raise RuntimeError('Codex 进程已退出，请重启 Web 服务')
            if self.tasks and self.tasks[0].done():
                raise RuntimeError('Codex 连接已断开，请重载 Codex')
            self.proc.stdin.write((json.dumps(message)+'\n').encode())
            await self.proc.stdin.drain()

    async def rpc(self, method, params, *, capture=None):
        self.counter += 1
        rid = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = (future, capture)
        try:
            await self.write({'id':rid, 'method':method, 'params':params})
            return await asyncio.wait_for(future, 60)
        finally:
            self.pending.pop(rid, None)

    async def read(self):
        try:
            async for line in _read_lines(self.proc.stdout):
                msg = json.loads(line)
                if 'method' in msg:
                    await self.on_message(msg)
                    continue
                entry = self.pending.get(msg.get('id'))
                if not entry:
                    continue
                future, capture = entry
                if future.done():
                    continue
                if 'error' in msg:
                    future.set_exception(RuntimeError(msg['error'].get('message', str(msg['error']))))
                else:
                    result = msg.get('result', {})
                    # Capture snapshot metadata before reading the next notification.
                    try:
                        future.set_result(capture(result) if capture else result)
                    except Exception as exc:
                        future.set_exception(exc)
        except Exception:
            log.exception('App Server reader failed')
        finally:
            for future, _ in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError('Codex 连接已断开，请重启服务'))
            self.on_disconnect()

    async def stderr(self):
        async for line in _read_lines(self.proc.stderr):
            log.warning('codex: %s', line.decode(errors='replace').rstrip())

    async def close(self):
        try:
            if self.proc and self.proc.returncode is None:
                if os.name == 'nt':
                    self.proc.stdin.close()
                else:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self.proc.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.proc.wait(), 5)
                except asyncio.TimeoutError:
                    if self.job:
                        self.job.close()
                    else:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(self.proc.pid, signal.SIGKILL)
                    await self.proc.wait()
        finally:
            if os.name != 'nt' and self.proc:
                # Descendants may outlive their app-server parent.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.proc.pid, signal.SIGKILL)
            if self.job:
                self.job.close()
                self.job = None
            for task in self.tasks:
                task.cancel()
            try:
                for task in self.tasks:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            finally:
                self.tasks = []
                self.proc = None
        # Request IDs remain monotonic across restarts: an old RPC's finally block
        # must never remove a new process's pending request.
