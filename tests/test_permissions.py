"""Permission changes use fake RPC and an isolated SQLite database."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from pydantic import ValidationError
from backend.codex_manager import CodexManager
from backend.session_manager import SessionStore
from backend.main import Message

class PermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=SessionStore(Path(self.temp.name)/'db')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.db.close)
        self.store.save('a',self.temp.name,'m','medium')
        self.store.save('b',self.temp.name,'m','medium')
        self.manager=CodexManager({'sandbox':'workspace-write'},self.store)
        self.manager.models=[{'id':'m','efforts':['medium']}]
        self.manager.loaded.add('a')
        self.manager.client.rpc=AsyncMock(return_value={'turn':{'id':'turn'},'thread':{}})

    async def test_modes_persist_and_resume_independently(self):
        for mode in ('read-only','danger-full-access','workspace-write'):
            self.manager.active.clear()
            self.manager.client.rpc.reset_mock()
            await self.manager.send('a','hello','m','medium',sandbox=mode)
            calls=self.manager.client.rpc.call_args_list
            self.assertEqual(calls[0].args[0],'thread/resume')
            self.assertEqual(calls[0].args[1]['sandbox'],mode)
            self.assertEqual(calls[0].args[1]['approvalPolicy'],'never' if mode=='danger-full-access' else 'on-request')
            self.assertEqual(calls[1].args[0],'turn/start')
            self.assertEqual(self.store.get('a')['sandbox'],mode)
            self.assertIsNone(self.store.get('b')['sandbox'])
            self.manager.loaded.clear()
            self.manager.client.rpc.reset_mock()
            await self.manager.resume('a')
            self.assertEqual(self.manager.client.rpc.call_args.args[1]['sandbox'],mode)
        other=SessionStore(Path(self.temp.name)/'db')
        try:self.assertEqual(other.get('a')['sandbox'],'workspace-write')
        finally:other.db.close()

    async def test_failed_switch_does_not_persist_or_start_turn(self):
        self.manager.client.rpc.side_effect=RuntimeError('fixture rejected')
        with self.assertRaises(RuntimeError):
            await self.manager.send('a','hello','m','medium',sandbox='danger-full-access')
        self.assertIsNone(self.store.get('a')['sandbox'])
        self.assertNotIn('a',self.manager.active)
        self.assertEqual(self.manager.client.rpc.await_count,1)

    async def test_active_and_invalid_modes_are_rejected(self):
        self.manager.active['a']='running'
        with self.assertRaises(ValueError):
            await self.manager.send('a','hello','m','medium',sandbox='read-only')
        self.manager.active.clear()
        with self.assertRaises(ValueError):
            await self.manager.send('a','hello','m','medium',sandbox='invalid')
        self.manager.client.rpc.assert_not_awaited()
        with self.assertRaises(ValidationError):
            Message(message='hello',model='m',effort='medium',sandbox='invalid')
