"""Session endpoints with temporary configuration and a fake Codex transport."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import Request
from backend import main
from backend.config import load_config
from backend.codex_manager import CodexManager

try:
    import httpx
except ImportError:
    httpx = None

import yaml
from pydantic import ValidationError


class SessionApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.workspace = root / 'workspace';self.workspace.mkdir()
        config = root / 'config.yaml'
        config.write_text(yaml.safe_dump(dict(username='fixture', password='fixture-only',
                         allowed_roots=[str(self.workspace)], data_dir=str(root / 'data'))))
        self.api = main
        with patch.dict(os.environ, {'CODEX_WEB_CONFIG': str(config)}):
            self.app = main.create_app(load_config())
        self.request = Request({'type':'http', 'app':self.app})
        self.addCleanup(self.temp.cleanup)

    async def asyncSetUp(self):
        with patch.object(CodexManager, 'start', new=AsyncMock()):
            lifespan = self.app.router.lifespan_context(self.app)
            await lifespan.__aenter__()
        self.addAsyncCleanup(lifespan.__aexit__, None, None, None)
        self.app.state.store.save('a', str(self.workspace), 'test', 'medium')

    @unittest.skipIf(httpx is None, 'Optional httpx dependency is not installed')
    async def test_editor_http_auth_security_and_conflict(self):
        file = self.workspace / 'sample.py'
        file.write_text('original')
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://localhost') as client:
            params = dict(workspace=str(self.workspace), path='sample.py')
            self.assertEqual((await client.get('/api/editor/index', params={'workspace':str(self.workspace)})).status_code, 401)
            self.assertEqual((await client.get('/api/account/limits')).status_code, 401)
            self.assertEqual((await client.get('/api/editor/file', params=params)).status_code, 401)
            login = await client.post('/api/login', headers={'X-Codex-Web':'1'}, json=dict(username='fixture', password='fixture-only'))
            self.assertEqual(login.status_code, 200)
            index = await client.get('/api/editor/index', params={'workspace':str(self.workspace)})
            self.assertEqual(index.json()['paths'], ['sample.py'])
            self.assertEqual((await client.get('/api/editor/index', params={'workspace':self.temp.name})).status_code, 400)
            read = await client.get('/api/editor/file', params=params)
            self.assertEqual(read.status_code, 200)
            body = dict(**params, content='edited', revision=read.json()['revision'])
            self.assertEqual((await client.put('/api/editor/file', json=body)).status_code, 403)
            self.assertEqual((await client.put('/api/editor/file', json=body, headers={'X-Codex-Web':'1','Origin':'http://untrusted.test'})).status_code, 403)
            saved = await client.put('/api/editor/file', json=body, headers={'X-Codex-Web':'1'})
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(file.read_text(), 'edited')
            self.assertEqual((await client.put('/api/editor/file', json=body, headers={'X-Codex-Web':'1'})).status_code, 409)
            params['path'] = '../config.yaml'
            self.assertEqual((await client.get('/api/editor/file', params=params)).status_code, 403)

    @unittest.skipIf(httpx is None, 'Optional httpx dependency is not installed')
    async def test_git_endpoints_require_login_and_are_get_only(self):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://localhost') as client:
            params = dict(workspace=str(self.workspace))
            self.assertEqual((await client.get('/api/git/status', params=params)).status_code, 401)
            self.assertEqual((await client.get('/api/git/diff', params=params)).status_code, 401)
            await client.post('/api/login', headers={'X-Codex-Web':'1'}, json=dict(username='fixture', password='fixture-only'))
            result = await client.get('/api/git/status', params=params)
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json()['repository'])
            self.assertEqual((await client.post('/api/git/status', headers={'X-Codex-Web':'1'})).status_code, 405)
            self.assertEqual((await client.put('/api/git/diff', headers={'X-Codex-Web':'1'})).status_code, 405)
            params['workspace'] = self.temp.name
            self.assertEqual((await client.get('/api/git/status', params=params)).status_code, 400)

    async def test_account_limits_use_durations_and_remaining_percent(self):
        async def rpc(method, params):
            self.assertEqual(method, 'account/rateLimits/read')
            return dict(rateLimits=dict(primary=dict(usedPercent=99, windowDurationMins=300)),
                rateLimitsByLimitId=dict(codex=dict(limitId='codex',
                    primary=dict(usedPercent=58, windowDurationMins=10080, resetsAt=2000000000),
                    secondary=dict(usedPercent=25, windowDurationMins=300, resetsAt=1900000000))))
        self.app.state.codex.client.rpc = rpc
        result = await self.api.account_limits(self.request)
        self.assertEqual(result['windows']['fiveHour']['remainingPercent'], 75)
        self.assertEqual(result['windows']['weekly']['remainingPercent'], 42)
        self.assertEqual(result['windows']['weekly']['resetsAt'], 2000000000)
        self.assertIsNotNone(result['updatedAt'])
        async def unavailable(*args):
            raise RuntimeError('upstream unavailable')
        self.app.state.codex.client.rpc = unavailable
        result = await self.api.account_limits(self.request)
        self.assertEqual(result['windows'], {})
        self.assertIsNone(result['updatedAt'])
        self.assertIn('暂不可用', result['message'])

    async def test_account_limits_do_not_guess_window_or_mix_switched_auth(self):
        calls = []
        async def rpc(method, params):
            calls.append(method)
            return dict(rateLimits=dict(primary=dict(usedPercent=0, windowDurationMins=15), secondary=None))
        self.app.state.codex.client.rpc = rpc
        result = await self.api.account_limits(self.request)
        self.assertEqual(result['windows'], {})
        self.app.state.codex._auth_marker = 'previous-account'
        result = await self.api.account_limits(self.request)
        self.assertEqual(len(calls), 1)
        self.assertIn('重载', result['message'])
        self.assertEqual(result['windows'], {})

    async def test_snapshot_recovers_latest_plan_at_cursor(self):
        store = self.app.state.store
        for step in ('旧计划', '当前计划'):
            store.append('a', dict(method='turn/plan/updated', params=dict(threadId='a', turnId='turn', plan=[dict(step=step, status='inProgress')])))
        cursor = store.latest_seq('a')
        store.append('a', dict(method='turn/plan/updated', params=dict(threadId='a', turnId='turn', plan=[dict(step='稍后更新', status='completed')])))
        store.save('b', str(self.workspace), 'test', 'medium')
        store.append('b', dict(method='turn/plan/updated', params=dict(turnId='turn', plan=[])))
        async def resume(sid):
            return store.get(sid)
        async def snapshot(*args, **kwargs):
            return dict(thread=dict(turns=[dict(id='turn', status='inProgress', items=[])])), cursor
        self.app.state.codex.resume = resume
        self.app.state.codex.client.rpc = snapshot
        result = await self.api.session(self.request, 'a')
        self.assertEqual(result['history']['turns'][0]['webPlan']['plan'][0]['step'], '当前计划')
        self.assertEqual(result['cursor'], cursor)
        store.append('a', dict(method='web/history', params=dict(turns=[dict(id='turn', items=[])])))
        restored = store.snapshot_events('a')
        self.assertEqual(restored[0]['params']['turns'][0]['webPlan']['plan'][0]['step'], '稍后更新')
        self.assertEqual(store.events('a', cursor)[0]['params']['plan'][0]['step'], '稍后更新')

    async def test_pin_validation_and_workspace_boundary(self):
        manager = self.app.state.codex
        manager.client.rpc = AsyncMock(side_effect=AssertionError('Pin must not call Codex'))
        manager.active['a'] = 'running-turn'
        result = await self.api.pin_session(self.request, 'a', self.api.SessionPin(pinned=True))
        self.assertEqual(result['pinned'], 1)
        result = await self.api.pin_session(self.request, 'a', self.api.SessionPin(pinned=False))
        self.assertEqual(result['pinned'], 0)
        with self.assertRaises(ValidationError): self.api.SessionPin(pinned='false')
        with self.assertRaises(KeyError):
            await self.api.pin_session(self.request, 'missing', self.api.SessionPin(pinned=True))
        self.app.state.store.save('outside', self.temp.name, 'test', 'medium')
        with self.assertRaises(ValueError):
            await self.api.pin_session(self.request, 'outside', self.api.SessionPin(pinned=True))
        self.app.state.store.archive('a')
        with self.assertRaises(ValueError):
            await self.api.pin_session(self.request, 'a', self.api.SessionPin(pinned=True))
        manager.active.clear()

    async def test_rename_validation_and_workspace_boundary(self):
        result = await self.api.rename_session(self.request, 'a', self.api.SessionName(title='  电源分析  '))
        self.assertEqual(result['title'], '电源分析')
        with self.assertRaises(ValueError):
            await self.api.rename_session(self.request, 'a', self.api.SessionName(title='   '))
        with self.assertRaises(ValidationError):
            self.api.SessionName(title='字' * 121)
        with self.assertRaises(KeyError):
            await self.api.rename_session(self.request, 'missing', self.api.SessionName(title='标题'))
        self.app.state.store.save('outside', self.temp.name, 'test', 'medium')
        with self.assertRaises(ValueError):
            await self.api.rename_session(self.request, 'outside', self.api.SessionName(title='标题'))

    async def test_send_returns_stable_title_and_preserves_manual_rename(self):
        manager = self.app.state.codex
        manager.loaded.add('a')
        manager.models = [dict(id='test', efforts=['medium'], service_tiers=[dict(id='priority',name='Fast',description='faster')])]
        async def rpc(method, params):
            self.assertEqual(method, 'turn/start')
            return {'turn': {'id': 'fixture-turn'}}
        manager.client.rpc = rpc
        async def send(text, service_tier='default'):
            result = await self.api.send(self.request, 'a', self.api.Message(message=text, model='test', effort='medium', service_tier=service_tier))
            manager.active.clear()
            return result
        self.assertEqual((await send('PSU 数值分析'))['title'], 'PSU 数值分析')
        self.assertEqual((await send('继续检查'))['title'], 'PSU 数值分析')
        await self.api.rename_session(self.request, 'a', self.api.SessionName(title='已确认标题'))
        self.assertEqual((await send('再次检查'))['title'], '已确认标题')
        await send('加速检查','priority')

    @unittest.skipIf(httpx is None, 'Optional httpx dependency is not installed')
    async def test_http_session_lifecycle_uses_business_operations(self):
        manager = self.app.state.codex
        manager.models = [dict(id='test', efforts=['medium'])]
        calls = []
        async def rpc(method, params, *, capture=None):
            calls.append((method, params))
            if method == 'thread/start':
                result = {'thread':{'id':'created'}}
            elif method in ('thread/read', 'thread/resume'):
                result = {'thread':{'id':params['threadId'], 'cwd':str(self.workspace), 'turns':[]}}
            elif method == 'thread/list':
                result = {'data':[{'id':'native','cwd':str(self.workspace)}, {'id':'outside','cwd':self.temp.name}], 'nextCursor':None}
            elif method == 'turn/start':
                result = {'turn':{'id':'turn-created'}}
            else:
                result = {}
            return capture(result) if capture else result
        manager.client.rpc = rpc
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url='http://localhost', headers={'X-Codex-Web':'1'}) as client:
            await client.post('/api/login', json=dict(username='fixture', password='fixture-only'))
            native = await client.get('/api/native-sessions')
            self.assertEqual([s['id'] for s in native.json()['data']], ['native'])
            imported = await client.post('/api/sessions/native/import', json=dict(model='test', effort='medium'))
            self.assertEqual(imported.json()['id'], 'native')
            created = await client.post('/api/sessions', json=dict(cwd=str(self.workspace), model='test', effort='medium'))
            self.assertEqual(created.json()['id'], 'created')
            base = '/api/sessions/created'
            sent = await client.post(base+'/messages', json=dict(message='first', model='test', effort='medium'))
            self.assertEqual(sent.json(), {'ok':True,'title':'first','sandbox':'workspace-write'})
            history = await client.get(base)
            self.assertTrue(history.json()['active'])
            self.assertEqual(history.json()['history']['id'], 'created')
            self.assertEqual((await client.post(base+'/archive')).status_code, 400)
            self.assertEqual((await client.post(base+'/stop')).status_code, 200)
            self.assertIn(('turn/interrupt', {'threadId':'created','turnId':'turn-created'}), calls)
            await manager.on_message({'method':'turn/completed','params':{'threadId':'created','turn':{'id':'turn-created'}}})
            renamed = await client.patch(base, json={'title':'renamed'})
            self.assertEqual(renamed.json()['title'], 'renamed')
            self.assertEqual((await client.post(base+'/archive')).status_code, 200)
            archived = await client.get('/api/sessions?archived=true')
            self.assertEqual([s['id'] for s in archived.json()], ['created'])
            self.assertEqual((await client.delete(base)).status_code, 200)
            self.assertEqual((await client.get(base)).status_code, 400)


if __name__ == '__main__':
    unittest.main()
