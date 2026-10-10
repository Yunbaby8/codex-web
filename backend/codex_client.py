"""Persistent stdio RPC transport. No workspace, session or database policy."""
import asyncio
import contextlib
import json
import logging
import os
import signal

log = logging.getLogger(__name__)


class CodexClient:
    def __init__(self, binary, on_message, on_disconnect):
        self.binary = binary
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self.proc = None
        self.tasks = []
        self.pending = {}
        self.counter = 0
        self.write_lock = asyncio.Lock()

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            self.binary, 'app-server', '--listen', 'stdio://',
            # The Web question dialog also needs the tool available in Default mode.
            '--enable', 'default_mode_request_user_input',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=8*1024*1024)
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
            while line := await self.proc.stdout.readline():
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
        while line := await self.proc.stderr.readline():
            log.warning('codex: %s', line.decode(errors='replace').rstrip())

    async def close(self):
        try:
            if self.proc and self.proc.returncode is None:
                os.killpg(self.proc.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.proc.wait(), 5)
                except asyncio.TimeoutError:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                    await self.proc.wait()
        finally:
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
