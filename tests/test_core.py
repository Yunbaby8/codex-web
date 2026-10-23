import asyncio
import base64
import json
import sqlite3
import tempfile
import unittest
from windows_fixture import junction
from pathlib import Path
from backend.workspace import Workspaces
from backend.session_manager import SessionStore
from backend.codex_manager import CodexManager

class WorkspaceTests(unittest.TestCase):
    def test_containment_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'root';root.mkdir();(root/'project').mkdir()
            outside=Path(temp)/'outside';outside.mkdir()
            junction(root/'escape', outside)
            junction(root/'inside', root/'project')
            (root/'file.txt').write_text('test')
            w=Workspaces([str(root)])
            for path in (root/'../outside',root/'escape',outside,root/'file.txt'):
                with self.assertRaises(ValueError):w.resolve(str(path))
            self.assertEqual(w.resolve(str(root/'inside')),(root/'project').resolve())
            self.assertEqual({d['name'] for d in w.browse(str(root))['directories']},{'inside','project'})
            self.assertIsNone(w.browse(str(root))['parent'])

    def test_file_containment_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'root';root.mkdir();inside=root/'file.txt';inside.write_text('ok')
            outside=Path(temp)/'outside.txt';outside.write_text('no')
            junction(root/'escape', Path(temp))
            w=Workspaces([str(root)])
            self.assertEqual(w.resolve_file(str(inside)),inside.resolve())
            with self.assertRaises(ValueError):w.resolve_file(str(outside))
            with self.assertRaises(ValueError):w.resolve_file(str(root/'escape'/'outside.txt'))
            with self.assertRaises(ValueError):w.resolve_file(str(root))

class StoreTests(unittest.TestCase):
    def test_pinning_migration_order_persistence_and_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'sessions.db'
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL, title TEXT NOT NULL, updated REAL NOT NULL)')
            db.execute("INSERT INTO sessions VALUES ('old','/tmp','m','medium','Existing title',1)")
            db.commit(); db.close()
            store = SessionStore(path)
            self.assertEqual(store.get('old')['pinned'], 0)
            store.save('new', '/tmp', 'm', 'medium')
            store.set_pinned('old', True)
            self.assertEqual([s['id'] for s in store.list()], ['old', 'new'])
            self.assertEqual(store.get('old')['updated'], 1)
            store.db.close()
            store = SessionStore(path)
            self.assertEqual(store.get('old')['pinned'], 1)
            self.assertEqual(store.get('old')['title'], 'Existing title')
            store.set_pinned('old', False)
            self.assertEqual([s['id'] for s in store.list()], ['new', 'old'])
            store.set_pinned('old', True)
            store.save('old', '/tmp', 'm', 'medium')
            self.assertEqual(store.get('old')['pinned'], 1)
            store.archive('old')
            self.assertEqual(store.get('old')['pinned'], 0)
            with self.assertRaises(ValueError): store.set_pinned('old', True)
            with self.assertRaises(KeyError): store.set_pinned('missing', True)
            store.db.close()

    def test_title_survives_followups_rename_and_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'sessions.db';store=SessionStore(path)
            store.save('a','/tmp','m','medium')
            store.save('a','/tmp','m','medium','PSU 数值异常分析')
            store.save('a','/tmp','m','high','继续检查')
            self.assertEqual(store.get('a')['title'],'PSU 数值异常分析')
            self.assertEqual(store.get('a')['effort'],'high')
            store.rename('a','New Session')
            store.db.close();store=SessionStore(path)
            store.save('a','/tmp','m','medium','不要覆盖手动标题')
            self.assertEqual(store.get('a')['title'],'New Session')
            with self.assertRaises(ValueError):store.rename('a','   ')
            with self.assertRaises(ValueError):store.rename('a','字'*121)
            with self.assertRaises(KeyError):store.rename('missing','标题')
            store.db.close()

    def test_title_migration_preserves_existing_named_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'sessions.db';db=sqlite3.connect(path)
            db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL, title TEXT NOT NULL, updated REAL NOT NULL)')
            db.execute("INSERT INTO sessions VALUES ('a','/tmp','m','medium','已有标题',1)")
            db.commit();db.close()
            store=SessionStore(path);store.save('a','/tmp','m','medium','追问')
            self.assertEqual(store.get('a')['title'],'已有标题')
            store.db.close()

    def test_adds_archived_column_to_existing_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'sessions.db';db=sqlite3.connect(path)
            db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL, title TEXT NOT NULL, updated REAL NOT NULL)')
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',('a','/tmp','model','medium','Existing',1));db.commit();db.close()
            store=SessionStore(path)
            self.assertEqual(store.list()[0]['archived'],0)
            store.archive('a');self.assertEqual(store.list(),[])
            store.db.close()

    def test_restart_and_replay_cursor(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'sessions.db';s=SessionStore(path)
            s.save('a','/tmp','model','medium');s.save('b','/tmp','model','medium')
            s.append('a',{'method':'first'});s.append('b',{'method':'private'});s.append('a',{'method':'second'})
            cursor=s.events('a')[0]['seq'];s.db.close();s=SessionStore(path)
            self.assertEqual([e['method'] for e in s.events('a',cursor)],['second'])
            self.assertEqual(s.latest_seq('a'),s.events('a')[-1]['seq'])
            self.assertEqual(s.latest_seq('missing'),0)
            s.append('a',{'method':'web/history','params':{'turns':[]}})
            s.append('a',{'method':'after-history'})
            self.assertEqual([e['method'] for e in s.snapshot_events('a')],['web/history','after-history'])
            s.append('a',{'method':'thread/tokenUsage/updated','params':{'tokenUsage':{'modelContextWindow':258400}}})
            self.assertEqual(s.latest_event('a','thread/tokenUsage/updated')['params']['tokenUsage']['modelContextWindow'],258400)
            self.assertIsNone(s.latest_event('a','missing'))
            self.assertEqual(s.get('a')['cwd'],'/tmp')
            s.archive('b');self.assertEqual([item['id'] for item in s.list()],['a'])
            self.assertEqual([item['id'] for item in s.list(True)],['b'])
            s.delete('a');self.assertEqual(s.events('a'),[])
            with self.assertRaises(KeyError):s.get('a')
            s.db.close()

class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_question_survives_completion_reload_and_answer(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db')
            store.save('a',temp,'m','medium')
            manager=CodexManager({'sandbox':'workspace-write'},store,
                attachments=Mock(for_session=Mock(return_value=[])),workspaces=Workspaces([temp]))
            manager.models=[{'id':'m','efforts':['medium']}];manager.loaded.add('a')
            item={'id':'question','type':'agentMessage','delivery':'async','text':'选择回答方式',
                  'questions':[{'title':'回答方式？','options':['简洁','详细']},{'title':'补充说明'}]}
            await manager.on_message({'method':'item/completed','params':{'threadId':'a','turnId':'t','item':item}})
            await manager.on_message({'method':'turn/completed','params':{'threadId':'a','turn':{'id':'t','status':'completed'}}})
            self.assertIn('async:question',manager.requests)
            calls=[]
            async def rpc(method,params,*,capture=None):
                calls.append((method,params))
                result={'thread':{'turns':[{'id':'t','items':[item]}]}} if method=='thread/read' else {'turn':{'id':'next'}}
                return capture(result) if capture else result
            manager.client.rpc=rpc
            manager.requests.clear()
            self.assertEqual(len((await manager.get_session('a'))['requests']),1)
            with self.assertRaises(ValueError):
                await manager.answer('a','async:question',{'answers':{'0':'简洁'}})
            await manager.answer('a','async:question',{'answers':{'0':'简洁','1':'无'}})
            self.assertEqual(calls[-1][0],'turn/start')
            self.assertIn('回答方式？\n简洁',calls[-1][1]['input'][0]['text'])
            self.assertEqual((await manager.get_session('a'))['requests'],[])
            self.assertIn('async:question',store.resolved_requests('a'))

            store.db.close()

    async def test_async_question_steers_running_turn_and_preserves_failed_reply(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db')
            store.save('a',temp,'m','medium')
            manager=CodexManager({'sandbox':'workspace-write'},store)
            req=manager.async_question('a',{'id':'q','type':'agentMessage','questions':[{'title':'选哪个？','options':['甲','乙']}]})
            manager.requests[req['id']]=req;manager.active['a']='running'
            manager.client.rpc=AsyncMock(side_effect=RuntimeError('暂时失败'))
            with self.assertRaises(RuntimeError):
                await manager.answer('a',req['id'],{'answers':{'0':'乙'}})
            self.assertIn(req['id'],manager.requests)
            manager.client.rpc=AsyncMock(return_value={})
            await manager.answer('a',req['id'],{'answers':{'0':'乙'}})
            args=manager.client.rpc.call_args.args
            self.assertEqual(args[0],'turn/steer')
            self.assertEqual(args[1]['expectedTurnId'],'running')
            self.assertIn('选哪个？\n乙',args[1]['input'][0]['text'])

            store.db.close()

    async def test_user_question_is_forwarded_and_answered(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db')
            store.save('a','/tmp','m','medium')
            manager=CodexManager({'sandbox':'danger-full-access'},store)
            manager.loaded.add('a')
            writes=[]
            async def write(message):
                writes.append(message)
            manager.client.write=write
            request={'id':7,'method':'item/tool/requestUserInput','params':{
                'threadId':'a','turnId':'t','itemId':'i','questions':[
                    {'id':'layout','question':'如何展示？','options':[{'label':'弹窗','description':'集中显示'}]},
                    {'id':'note','question':'补充要求','options':[]}]}}
            await manager.on_message(request)
            self.assertEqual(manager.requests['7'],request)
            self.assertEqual(store.events('a')[-1]['params'],request)
            self.assertEqual(writes,[])
            with self.assertRaises(ValueError):
                await manager.answer('other','7',{'answers':{}})
            await manager.answer('a','7',{'answers':{'layout':'弹窗','note':'保持简洁'}})
            self.assertEqual(writes,[{'id':7,'result':{'answers':{
                'layout':{'answers':['弹窗']},'note':{'answers':['保持简洁']}}}}])
            self.assertEqual(manager.requests,{})
            self.assertEqual(store.events('a')[-1]['method'],'web/requestResolved')

            store.db.close()

    def test_auth_label_matches_auth_snapshot_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);accounts=root/'accounts';accounts.mkdir()
            def write_auth(path, account_id, subject):
                header=base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b'=').decode()
                payload=base64.urlsafe_b64encode(json.dumps({'sub':subject}).encode()).rstrip(b'=').decode()
                path.write_text(json.dumps({'auth_mode':'chatgpt','tokens':{
                    'account_id':account_id,'id_token':f'{header}.{payload}.signature'}}))
            active=root/'auth.json';write_auth(active,'account-a','subject-a')
            write_auth(accounts/'additional.auth.json','account-a','subject-a')
            write_auth(accounts/'personal.auth.json','account-b','subject-b')
            store=SessionStore(root/'db')
            manager=CodexManager({'sandbox':'workspace-write','auth_file':active,'auth_accounts_dir':accounts},store)
            self.assertEqual(manager.auth_user(),{'label':'additional','mode':'chatgpt'})
            store.db.close()

    async def test_full_access_disables_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db')
            manager=CodexManager({'sandbox':'danger-full-access'},store)
            config=manager.thread_config('/tmp','m')
            self.assertEqual(config['sandbox'],'danger-full-access')
            self.assertEqual(config['approvalPolicy'],'never')
            store.db.close()

    async def test_concurrent_send_rejected_and_stop_targets_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db');store.save('a','/tmp','m','medium')
            manager=CodexManager({'sandbox':'workspace-write'},store)
            manager.models=[{'id':'m','efforts':['medium']}];manager.loaded.add('a')
            calls=[]
            async def rpc(method,params):
                calls.append((method,params));await asyncio.sleep(.01)
                return {'turn':{'id':'turn1'}}
            manager.client.rpc=rpc
            results=await asyncio.gather(manager.send('a','hello','m','medium'),manager.send('a','duplicate','m','medium'),return_exceptions=True)
            self.assertEqual(sum(isinstance(r,ValueError) for r in results),1)
            self.assertEqual(sum(m=='turn/start' for m,p in calls),1)
            self.assertEqual(next(p for m,p in calls if m=='turn/start')['serviceTierForTurn'],'default')
            await manager.stop('a')
            self.assertEqual(calls[-1],('turn/interrupt',{'threadId':'a','turnId':'turn1'}))
            store.db.close()

    async def test_fast_tier_is_validated_and_forwarded_for_one_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db');store.save('a','/tmp','m','medium')
            manager=CodexManager({'sandbox':'workspace-write'},store)
            manager.models=[{'id':'m','efforts':['medium'],'service_tiers':[{'id':'priority','name':'Fast','description':'faster'}]}];manager.loaded.add('a')
            calls=[]
            async def rpc(method,params):calls.append((method,params));return {'turn':{'id':'turn1'}}
            manager.client.rpc=rpc
            await manager.send('a','hello','m','medium','priority')
            self.assertEqual(calls[0][1]['serviceTierForTurn'],'priority')
            manager.active.clear()
            with self.assertRaisesRegex(ValueError,'不支持'):
                await manager.send('a','hello','m','medium','unknown')
            store.db.close()

    async def test_restart_clears_app_server_state_and_keeps_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db');store.save('a','/tmp','m','medium')
            manager=CodexManager({'sandbox':'workspace-write'},store)
            manager.loaded.add('a');manager.requests['1']={'method':'request'};manager.client.counter=9
            calls=[]
            async def close(): calls.append('close')
            async def start():
                calls.append('start')
                manager.models=[{'id':'new-model','efforts':['medium']}]
            manager.close=close;manager.start=start
            models=await manager.restart()
            self.assertEqual(calls,['close','start'])
            self.assertEqual(manager.loaded,set())
            self.assertEqual(manager.requests,{})
            self.assertEqual(manager.client.counter,9)
            self.assertEqual(models[0]['id'],'new-model')
            self.assertEqual(store.get('a')['cwd'],'/tmp')
            store.db.close()

    async def test_restart_rejects_active_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db')
            manager=CodexManager({'sandbox':'workspace-write'},store);manager.active['a']='turn1'
            with self.assertRaises(ValueError):await manager.restart()
            store.db.close()

    async def test_archive_and_delete_thread_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db')
            manager=CodexManager({'sandbox':'workspace-write'},store)
            manager.loaded.update(('a','b'));manager.requests={'1':{'params':{'threadId':'a'}},'2':{'params':{'threadId':'b'}}}
            calls=[]
            async def rpc(method,params):calls.append((method,params));return {}
            manager.client.rpc=rpc
            await manager.archive('a');await manager.delete('b')
            self.assertEqual(calls,[('thread/archive',{'threadId':'a'}),('thread/delete',{'threadId':'b'})])
            self.assertEqual(manager.loaded,set());self.assertEqual(manager.requests,{})
            manager.active['c']='turn1'
            with self.assertRaises(ValueError):await manager.archive('c')
            with self.assertRaises(ValueError):await manager.delete('c')
            store.db.close()

    async def test_delete_missing_rollout_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db')
            manager=CodexManager({'sandbox':'workspace-write'},store)
            manager.loaded.add('a');manager.requests={'1':{'params':{'threadId':'a'}}}
            async def rpc(method,params):
                raise RuntimeError('no rollout found for thread id a')
            manager.client.rpc=rpc
            await manager.delete('a')
            self.assertEqual(manager.loaded,set())
            self.assertEqual(manager.requests,{})
            store.db.close()

    async def test_delete_does_not_hide_other_rpc_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db')
            manager=CodexManager({'sandbox':'workspace-write'},store)
            manager.loaded.add('a');manager.requests={'1':{'params':{'threadId':'a'}}}
            async def rpc(method,params):
                raise RuntimeError('Codex 连接已断开')
            manager.client.rpc=rpc
            with self.assertRaisesRegex(RuntimeError,'Codex 连接已断开'):
                await manager.delete('a')
            self.assertEqual(manager.loaded,{'a'})
            self.assertEqual(set(manager.requests),{'1'})
            store.db.close()

    async def test_auth_change_reloads_only_after_active_turn_finishes(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db')
            manager=CodexManager({'sandbox':'workspace-write','auth_poll_seconds':.01},store)
            manager._auth_marker=('old',)
            manager.auth_marker=lambda:('new',)
            manager.active['a']='turn1'
            reloads=[]
            async def restart():reloads.append('restart')
            manager.restart=restart
            watcher=asyncio.create_task(manager.watch_auth())
            await asyncio.sleep(.03)
            self.assertEqual(reloads,[])
            manager.active.clear()
            await asyncio.sleep(.03)
            self.assertEqual(reloads,['restart'])
            self.assertEqual(manager._auth_marker,('new',))
            watcher.cancel()
            with self.assertRaises(asyncio.CancelledError):await watcher
            store.db.close()

    async def test_steer_targets_current_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db');store.save('a','/tmp','m','medium')
            manager=CodexManager({'sandbox':'workspace-write'},store);manager.active['a']='turn1'
            calls=[]
            async def rpc(method,params):calls.append((method,params));return {'turnId':'turn1'}
            manager.client.rpc=rpc
            attachment={'type':'localImage','path':'/tmp/image.png'}
            await manager.steer('a','改为检查这里',[attachment])
            self.assertEqual(calls,[('turn/steer',{
                'threadId':'a','expectedTurnId':'turn1',
                'input':[{'type':'text','text':'改为检查这里'},attachment],
            })])
            event=store.events('a')[-1]
            self.assertEqual(event['method'],'web/steer')
            self.assertEqual(event['params']['turnId'],'turn1')
            self.assertTrue(event['params']['item']['webSteer'])
            self.assertEqual(event['params']['item']['content'],[{'type':'text','text':'改为检查这里'},attachment])
            store.db.close()

    async def test_steer_rejects_without_active_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SessionStore(Path(temp)/'db');manager=CodexManager({'sandbox':'workspace-write'},store)
            with self.assertRaises(ValueError):await manager.steer('a','继续')
            store.db.close()

class StopConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.store = SessionStore(root/'db')
        self.addCleanup(self.store.db.close)
        self.manager = CodexManager({'sandbox':'workspace-write', 'auth_file':root/'auth.json',
                                     'auth_accounts_dir':root/'accounts'}, self.store)
        self.manager.models = [{'id':'m','efforts':['medium']}]
        for sid in ('a', 'b'):
            self.store.save(sid, str(root), 'm', 'medium')
            self.manager.loaded.add(sid)

    async def test_stop_does_not_wait_for_account_limits(self):
        manager = self.manager
        manager.active['a'] = 'turn-a'
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def rpc(method, params):
            calls.append((method, params))
            if method == 'account/rateLimits/read':
                entered.set()
                await release.wait()
            return {}
        manager.client.rpc = rpc
        query = asyncio.create_task(manager.account_limits())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(manager.stop('a'), 1)
            self.assertFalse(query.done())
            self.assertEqual(calls[-1], ('turn/interrupt', {'threadId':'a','turnId':'turn-a'}))
            self.assertEqual(manager.active['a'], 'turn-a')
        finally:
            release.set()
            await query

    async def test_stop_does_not_wait_for_other_session_start(self):
        manager = self.manager
        manager.active['a'] = 'turn-a'
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def rpc(method, params):
            calls.append((method, params))
            if method == 'turn/start':
                entered.set()
                await release.wait()
                return {'turn':{'id':'turn-b'}}
            return {}
        manager.client.rpc = rpc
        sending = asyncio.create_task(manager.send('b', 'hello', 'm', 'medium'))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(manager.stop('a'), 1)
            self.assertFalse(sending.done())
            self.assertEqual(calls[-1], ('turn/interrupt', {'threadId':'a','turnId':'turn-a'}))
        finally:
            release.set()
            await sending
        self.assertEqual(manager.active['b'], 'turn-b')

    async def test_stop_waits_for_own_start_to_obtain_turn_id(self):
        manager = self.manager
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def rpc(method, params):
            calls.append((method, params))
            if method == 'turn/start':
                entered.set()
                await release.wait()
                return {'turn':{'id':'turn-a'}}
            return {}
        manager.client.rpc = rpc
        sending = asyncio.create_task(manager.send('a', 'hello', 'm', 'medium'))
        stopping = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            stopping = asyncio.create_task(manager.stop('a'))
            await asyncio.sleep(0)
            self.assertFalse(stopping.done())
            self.assertEqual([method for method, _ in calls], ['turn/start'])
        finally:
            release.set()
            await sending
            if stopping:
                await asyncio.wait_for(stopping, 1)
        self.assertEqual(calls[-1], ('turn/interrupt', {'threadId':'a','turnId':'turn-a'}))

    async def test_interrupt_does_not_allow_reload_before_turn_completes(self):
        manager = self.manager
        manager.active['a'] = 'turn-a'
        entered, release = asyncio.Event(), asyncio.Event()
        async def rpc(method, params):
            self.assertEqual(method, 'turn/interrupt')
            entered.set()
            await release.wait()
            return {}
        manager.client.rpc = rpc
        stopping = asyncio.create_task(manager.stop('a'))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            with self.assertRaises(ValueError):
                await asyncio.wait_for(manager.restart(), 1)
        finally:
            release.set()
            await stopping
        with self.assertRaises(ValueError):
            await manager.restart()
        self.assertEqual(manager.active['a'], 'turn-a')

if __name__=='__main__':unittest.main()
