import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.main import create_app
from backend.codex_manager import CodexManager


class AppLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.config = dict(allowed_roots=[str(root)], data_dir=root/'data',
                           auth_file=root/'auth.json', auth_accounts_dir=root/'accounts')

    def test_app_creation_does_not_load_configuration_or_open_database(self):
        with patch('backend.main.load_config', side_effect=AssertionError('eager config')), \
             patch('backend.main.SessionStore', side_effect=AssertionError('eager database')):
            app = create_app()
        self.assertFalse(hasattr(app.state, 'store'))
        self.assertFalse(self.config['data_dir'].exists())

    async def test_each_lifespan_opens_and_closes_its_own_resources(self):
        app = create_app(self.config)
        with patch.object(CodexManager, 'start', new=AsyncMock()):
            async with app.router.lifespan_context(app):
                original = app.state.store
                original.save('a', self.config['allowed_roots'][0], 'm', 'medium')
                app.state.logins['old-login'] = 1
            with self.assertRaises(sqlite3.ProgrammingError): original.get('a')
            async with app.router.lifespan_context(app):
                self.assertIsNot(app.state.store, original)
                self.assertEqual(app.state.store.get('a')['model'], 'm')
                self.assertEqual(app.state.logins, {})
                self.assertEqual(app.state.codex.active, {})

    async def test_start_failure_still_closes_manager_and_database(self):
        app = create_app(self.config)
        with patch.object(CodexManager, 'start', new=AsyncMock(side_effect=RuntimeError('start failed'))), \
             patch.object(CodexManager, 'close', new=AsyncMock()) as close:
            with self.assertRaisesRegex(RuntimeError, 'start failed'):
                async with app.router.lifespan_context(app):
                    self.fail('startup should fail')
            close.assert_awaited_once()
        with self.assertRaises(sqlite3.ProgrammingError): app.state.store.list()

    async def test_close_failure_still_closes_database(self):
        app = create_app(self.config)
        with patch.object(CodexManager, 'start', new=AsyncMock()), \
             patch.object(CodexManager, 'close', new=AsyncMock(side_effect=RuntimeError('close failed'))):
            with self.assertRaisesRegex(RuntimeError, 'close failed'):
                async with app.router.lifespan_context(app):
                    pass
        with self.assertRaises(sqlite3.ProgrammingError): app.state.store.list()
