"""Exercise the real stdio transport against an offline Python child process."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.codex_client import CodexClient, _read_lines


FIXTURE = r'''
import json, sys
held = None
def emit(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method, rid = message.get('method'), message.get('id')
    if rid is None:
        continue
    if method == 'hold':
        held = rid
        emit({'method':'held','params':{}})
    elif method == 'release':
        emit({'id':rid,'result':{'released':True}})
        emit({'id':held,'result':{'held':True}})
    elif method == 'snapshot':
        emit({'method':'before','params':{}})
        emit({'id':rid,'result':{'snapshot':True}})
        emit({'method':'after','params':{}})
    elif method == 'error':
        emit({'id':rid,'error':{'message':'fixture rejected'}})
    elif method == 'die':
        sys.exit(0)
    elif method == 'large':
        emit({'id':rid,'result':{'text':'x' * (9 * 1024 * 1024)}})
        emit({'method':'after','params':{}})
    elif method == 'broken':
        print('{invalid json}', flush=True)
    else:
        emit({'id':rid,'result':message.get('params',{})})
'''


class CodexClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        binary = Path(temp.name)/'fake-codex.py'
        binary.write_text('#!'+sys.executable+'\n'+FIXTURE)
        binary.chmod(0o700)
        self.messages = []
        self.held, self.after = asyncio.Event(), asyncio.Event()
        self.disconnections = []
        async def on_message(message):
            self.messages.append(message['method'])
            if message['method'] == 'held': self.held.set()
            if message['method'] == 'after': self.after.set()
        self.client = CodexClient(sys.executable, on_message, lambda: self.disconnections.append(True))
        real_spawn = asyncio.create_subprocess_exec
        async def spawn(program, *args, **kwargs):
            return await real_spawn(program, str(binary), **kwargs)
        patcher = patch('backend.codex_client.asyncio.create_subprocess_exec', side_effect=spawn)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addAsyncCleanup(self.client.close)
        await self.client.start()

    async def test_out_of_order_replies_and_rpc_error(self):
        held = asyncio.create_task(self.client.rpc('hold', {}))
        await asyncio.wait_for(self.held.wait(), 2)
        released = await self.client.rpc('release', {})
        self.assertEqual(released, {'released':True})
        self.assertEqual(await held, {'held':True})
        with self.assertRaisesRegex(RuntimeError, 'fixture rejected'):
            await self.client.rpc('error', {})
        self.assertEqual(self.client.pending, {})

    async def test_snapshot_captures_cursor_before_next_notification(self):
        snapshot, cursor = await self.client.rpc('snapshot', {}, capture=lambda data: (data, len(self.messages)))
        await asyncio.wait_for(self.after.wait(), 2)
        self.assertEqual(snapshot, {'snapshot':True})
        self.assertEqual(cursor, 1)
        self.assertEqual(self.messages, ['before', 'after'])

    async def test_cancelled_rpc_ignores_late_reply(self):
        held = asyncio.create_task(self.client.rpc('hold', {}))
        await asyncio.wait_for(self.held.wait(), 2)
        held.cancel()
        with self.assertRaises(asyncio.CancelledError): await held
        self.assertEqual(await self.client.rpc('release', {}), {'released':True})
        self.assertEqual(await self.client.rpc('echo', {'still':'connected'}), {'still':'connected'})
        self.assertEqual(self.client.pending, {})

    async def test_large_snapshot_keeps_connection_and_following_messages(self):
        result = await asyncio.wait_for(self.client.rpc('large', {}), 5)
        self.assertEqual(len(result['text']), 9 * 1024 * 1024)
        await asyncio.wait_for(self.after.wait(), 2)
        self.assertEqual(await self.client.rpc('echo', {'still':'connected'}), {'still':'connected'})
        self.assertEqual(self.disconnections, [])

    async def test_reader_failure_rejects_new_rpc_without_waiting_for_timeout(self):
        with patch('backend.codex_client.log.exception'):
            with self.assertRaisesRegex(RuntimeError, '连接已断开'):
                await asyncio.wait_for(self.client.rpc('broken', {}), 2)
        self.assertIsNone(self.client.proc.returncode)
        with self.assertRaisesRegex(RuntimeError, '连接已断开'):
            await asyncio.wait_for(self.client.rpc('echo', {}), 2)
        self.assertEqual(self.client.pending, {})

    async def test_line_framing_handles_fragmented_unicode_and_final_line(self):
        stream = asyncio.StreamReader(limit=4)
        async def feed():
            for chunk in (b'{"text":"\xe7', b'\x95\x8c"}\nfirst\npar', b'tial'):
                stream.feed_data(chunk)
                await asyncio.sleep(0)
            stream.feed_eof()
        feeder = asyncio.create_task(feed())
        lines = [bytes(line) async for line in _read_lines(stream)]
        await feeder
        self.assertEqual(lines, ['{"text":"界"}'.encode(), b'first', b'partial'])

    async def test_exit_fails_pending_rpc_and_restart_uses_new_process(self):
        old_process = self.client.proc
        with self.assertRaisesRegex(RuntimeError, '连接已断开'):
            await self.client.rpc('die', {})
        await self.client.close()
        self.assertIsNotNone(old_process.returncode)
        self.assertEqual(self.disconnections, [True])
        self.assertEqual(self.client.pending, {})
        self.assertEqual(self.client.tasks, [])
        previous_id = self.client.counter
        await self.client.start()
        self.assertIsNot(self.client.proc, old_process)
        self.assertGreater(self.client.counter, previous_id)
        self.assertEqual(await self.client.rpc('echo', {'new':True}), {'new':True})


if __name__ == '__main__':
    unittest.main()
