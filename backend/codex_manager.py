"""Codex session policy, account reload and durable event handling."""
import asyncio
import base64
import json
import logging
import math
import time
import uuid
from pathlib import Path
from .codex_client import CodexClient

log = logging.getLogger(__name__)

class CodexManager:
    def __init__(self, config, store, attachments=None, workspaces=None):
        self.config, self.store = config, store
        self.attachments = attachments
        self.turn_images = {}
        self.requests, self.active, self.loaded = {}, {}, set()
        self.workspaces = workspaces
        self.client = CodexClient(config.get('codex_binary', 'codex'), self.on_message, self.on_disconnect)
        self.operation_lock = asyncio.Lock()
        self.auth_file = Path(config.get('auth_file', Path.home() / '.codex' / 'auth.json'))
        self.auth_accounts_dir = Path(config.get('auth_accounts_dir', Path.home() / '.codex-auth-snap' / 'accounts'))
        self.auth_poll_seconds = config.get('auth_poll_seconds', 1)
        self._auth_marker = self.auth_marker()
        self.running_auth_user = self.auth_user()

    def auth_marker(self):
        try:
            stat = self.auth_file.stat()
            return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
        except FileNotFoundError:
            return None

    @staticmethod
    def _auth_identity(path):
        try:
            auth = json.loads(Path(path).read_text(encoding='utf-8'))
            if auth.get('auth_mode') != 'chatgpt':
                return None
            tokens = auth.get('tokens') or {}
            token = tokens.get('id_token')
            if not isinstance(token, str) or token.count('.') != 2 or not tokens.get('account_id'):
                return None
            encoded = token.split('.')[1] + '=' * (-len(token.split('.')[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(encoded))
            subject = claims.get('sub')
            return (str(tokens['account_id']), str(subject)) if subject else None
        except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            return None

    def auth_account_label(self):
        active = self._auth_identity(self.auth_file)
        if not active:
            return ''
        try:
            matches = [
                path.name.removesuffix('.auth.json')
                for path in self.auth_accounts_dir.glob('*.auth.json')
                if self._auth_identity(path) == active
            ]
        except OSError:
            return ''
        return matches[0] if len(matches) == 1 else ''

    def auth_user(self):
        try:
            auth = json.loads(self.auth_file.read_text(encoding='utf-8'))
            mode = str(auth.get('auth_mode') or 'unknown')
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            mode = 'unknown'
        return {'label':self.auth_account_label(), 'mode':mode}

    async def account_limits(self):
        async with self.operation_lock:
            result = dict(authUser=self.running_auth_user, windows={}, updatedAt=None, message='')
            marker = self.auth_marker()
            if marker != self._auth_marker:
                result['message'] = '账号配置已变化，等待当前任务结束后重载 Codex，再刷新额度'
                return result
            try:
                data = await asyncio.wait_for(self.client.rpc('account/rateLimits/read', {}), 15)
            except (RuntimeError, asyncio.TimeoutError):
                result['message'] = '额度暂不可用，请稍后刷新；当前登录方式或服务可能不支持额度查询'
                return result
            if self.auth_marker() != marker:
                result['message'] = '账号配置正在变化，请重载后重新查询额度'
                return result
            buckets = data.get('rateLimitsByLimitId') or {}
            bucket = buckets.get('codex') or data.get('rateLimits') or {}
            if bucket.get('limitId') not in (None, 'codex'):
                bucket = {}
            for window in (bucket.get('primary'), bucket.get('secondary')):
                if not isinstance(window, dict):
                    continue
                duration, used = window.get('windowDurationMins'), window.get('usedPercent')
                key = {300:'fiveHour', 10080:'weekly'}.get(duration)
                if not key or isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used):
                    continue
                reset = window.get('resetsAt')
                result['windows'][key] = dict(remainingPercent=round(max(0, min(100, 100-used)), 1),
                    resetsAt=reset if isinstance(reset, (int, float)) and not isinstance(reset, bool) and math.isfinite(reset) else None)
            result['updatedAt'] = int(time.time())
            if not result['windows']:
                result['message'] = '接口未提供 5 小时或每周额度，暂不可用'
            return result

    async def start(self):
        start_marker, start_user = self.auth_marker(), self.auth_user()
        await self.client.start()
        result = await self.client.rpc('model/list', {})
        self.models = self.config['models'] or [
            {'id':m['model'], 'efforts':[e['reasoningEffort'] for e in m['supportedReasoningEfforts']],
             'default_effort':m['defaultReasoningEffort'], 'service_tiers':m.get('serviceTiers',[])} for m in result['data']]
        self._auth_marker = start_marker
        self.running_auth_user = start_user

    async def watch_auth(self):
        while True:
            await asyncio.sleep(self.auth_poll_seconds)
            marker = self.auth_marker()
            if marker == self._auth_marker or self.active:
                continue
            try:
                await self.restart()
            except ValueError:
                continue
            except Exception:
                log.exception('Failed to reload App Server after auth.json changed')
            else:
                self._auth_marker = marker
                log.info('Reloaded App Server after auth.json changed')

    def cleanup_images(self, sid):
        paths = self.turn_images.pop(sid, set())
        if self.attachments and paths:
            try:
                self.attachments.delete_images(sid, paths)
            except (OSError, ValueError):
                log.exception('Failed to remove temporary images for session %s', sid)

    def validate_model(self, model, effort):
        if not any(m['id']==model and effort in m['efforts'] for m in self.models):
            raise ValueError('模型或 Reasoning 不在后端允许列表中')

    def validate_service_tier(self, model, service_tier):
        if service_tier == 'default':
            return
        selected = next((m for m in self.models if m['id'] == model), None)
        if not selected or service_tier not in {tier['id'] for tier in selected.get('service_tiers', [])}:
            raise ValueError('当前模型不支持所选加速模式')

    def thread_config(self, cwd, model, sandbox=None):
        sandbox=sandbox or self.config['sandbox']
        return {'cwd':cwd,'model':model,'sandbox':sandbox,
                'approvalPolicy':'never' if sandbox=='danger-full-access' else 'on-request',
                'approvalsReviewer':'user'}

    async def create(self, cwd, model, effort):
        async with self.operation_lock:
            cwd = self.workspaces.resolve(cwd).as_posix()
            self.validate_model(model, effort)
            result = await self.client.rpc('thread/start', self.thread_config(cwd,model))
            sid = result['thread']['id']
            self.store.save(sid,cwd,model,effort)
            self.loaded.add(sid)
            return self.store.get(sid)

    async def resume(self, sid):
        session = self.store.get(sid)
        if sid not in self.loaded:
            result = await self.client.rpc('thread/resume', {'threadId':sid, **self.thread_config(session['cwd'],session['model'],session.get('sandbox'))})
            self.loaded.add(sid)
            self.store.append(sid, {'method':'web/history','params':result['thread']})
        return session

    async def send(self, sid, message, model, effort, service_tier='default', attachments=None, sandbox=None):
        async with self.operation_lock:
            if sid in self.active:
                raise ValueError('当前会话正在执行，请先 Stop 或等待完成')
            self.validate_model(model,effort)
            self.validate_service_tier(model,service_tier)
            if sandbox is not None and sandbox not in ('read-only', 'workspace-write', 'danger-full-access'):
                raise ValueError('无效的权限模式')
            session = await self.resume(sid)
            if sandbox is not None and sandbox != (session.get('sandbox') or self.config['sandbox']):
                await self.client.rpc('thread/resume', {'threadId':sid, **self.thread_config(session['cwd'],model,sandbox)})
                self.store.set_sandbox(sid, sandbox)
            self.active[sid] = None
            self.turn_images.setdefault(sid, set()).update(
                item['path'] for item in (attachments or []) if item['type'] == 'localImage')
            try:
                result = await self.client.rpc('turn/start', {'threadId':sid,'input':[{'type':'text','text':message}] + (attachments or []), 'model':model,'effort':effort,'serviceTierForTurn':service_tier})
                if sid in self.active:
                    self.active[sid] = result['turn']['id']
                self.store.save(sid,session['cwd'],model,effort,message[:60])
            except Exception:
                self.active.pop(sid,None)
                raise

    async def steer(self, sid, message, attachments=None):
        async with self.operation_lock:
            turn = self.active.get(sid)
            if not turn:
                raise ValueError('当前没有可引导的执行任务，请正常发送消息')
            effective_message = message or '请根据新上传的附件调整当前任务。'
            self.turn_images.setdefault(sid, set()).update(
                item['path'] for item in (attachments or []) if item['type'] == 'localImage')
            await self.client.rpc('turn/steer', {
                'threadId':sid,
                'expectedTurnId':turn,
                'input':[{'type':'text','text':effective_message}] + (attachments or []),
            })
            content = ([{'type':'text','text':message}] if message else []) + (attachments or [])
            self.store.append(sid, {'method':'web/steer','params':{
                'threadId':sid,'turnId':turn,
                'item':{'id':'web-steer-'+uuid.uuid4().hex,'type':'userMessage','webSteer':True,'content':content},
            }})

    async def stop(self, sid):
        turn = self.active.get(sid)
        if not turn:
            # A start/resume may still be obtaining the turn ID. Keep that ordering.
            async with self.operation_lock:
                turn = self.active.get(sid)
        if turn:
            # Interrupt known turns without waiting for unrelated RPC responses.
            # Leave active intact until completion; restart must still reject running turns.
            await self.client.rpc('turn/interrupt', {'threadId':sid,'turnId':turn})

    async def archive(self, sid):
        if sid in self.active:
            raise ValueError('运行中的会话不能归档，请先 Stop 或等待完成')
        await self.client.rpc('thread/archive', {'threadId':sid})
        self.loaded.discard(sid)
        self.requests = {rid:req for rid,req in self.requests.items() if req['params'].get('threadId') != sid}

    async def delete(self, sid):
        if sid in self.active:
            raise ValueError('运行中的会话不能删除，请先 Stop 或等待完成')
        try:
            await self.client.rpc('thread/delete', {'threadId':sid})
        except RuntimeError as exc:
            if str(exc) != f'no rollout found for thread id {sid}':
                raise
        self.loaded.discard(sid)
        self.requests = {rid:req for rid,req in self.requests.items() if req['params'].get('threadId') != sid}

    async def answer(self, sid, rid, answer):
        req = self.requests.get(rid)
        if not req or req['params'].get('threadId') != sid:
            raise ValueError('该请求已经结束')
        if req['method'] == 'web/asyncUserInput':
            answers = answer.get('answers', {})
            questions = req['params']['questions']
            if any(not str(answers.get(q['id'], '')).strip() for q in questions):
                raise ValueError('请回答每个问题后再提交。')
            message = '对 Codex 提问的回答：\n' + '\n'.join(
                f"{q['question']}\n{answers[q['id']]}" for q in questions)
            if sid in self.active:
                await self.steer(sid, message)
            else:
                session = self.store.get(sid)
                await self.send(sid, message, session['model'], session['effort'])
        elif req['method'].endswith('requestApproval'):
            if answer.get('decision') not in ('accept','decline','cancel'):
                raise ValueError('无效审批选项')
            result = {'decision':answer['decision']}
        else:
            answers = answer.get('answers', {})
            result = {'answers':{q['id']:{'answers':[str(answers.get(q['id'],''))]} for q in req['params']['questions']}}
        if req['method'] != 'web/asyncUserInput':
            await self.client.write({'id':req['id'],'result':result})
        self.requests.pop(rid,None)
        self.store.append(sid, {'method':'web/requestResolved','params':{'id':rid}})

    async def restart(self):
        async with self.operation_lock:
            if self.active:
                raise ValueError('仍有会话正在执行，请等待完成或 Stop 后再重新加载 Codex')
            await self.close()
            self.requests.clear()
            self.loaded.clear()
            await self.start()
            return self.models

    async def close(self):
        await self.client.close()

    @staticmethod
    def async_question(sid, item):
        if item.get('type') != 'agentMessage' or not item.get('questions'):
            return None
        return {'id':'async:'+item['id'], 'method':'web/asyncUserInput', 'params':{
            'threadId':sid, 'questions':[
                {'id':str(index), 'question':q['title'],
                 'options':[{'label':option} for option in q.get('options') or []]}
                for index,q in enumerate(item['questions'])]}}

    async def on_message(self, msg):
        method, params = msg['method'], msg.get('params', {})
        sid = params.get('threadId')
        if 'id' in msg:
            if sid in self.loaded and method in ('item/commandExecution/requestApproval','item/fileChange/requestApproval','item/tool/requestUserInput'):
                self.requests[str(msg['id'])] = msg
                self.store.append(sid, {'method':'web/request','params':msg})
            else:
                await self.client.write({'id':msg['id'], 'error':{'code':-32601,'message':'Web MVP does not support this request'}})
            return
        if sid not in self.loaded:
            return
        if method == 'item/completed':
            request = self.async_question(sid, params['item'])
            if request:
                self.requests[request['id']] = request
                self.store.append(sid, {'method':'web/request','params':request})
        if method == 'turn/started':
            self.active[sid] = params['turn']['id']
        elif method == 'turn/completed':
            self.cleanup_images(sid)
            self.active.pop(sid, None)
            self.requests = {k:v for k,v in self.requests.items()
                             if v['params'].get('threadId') != sid or v['method'] == 'web/asyncUserInput'}
        self.store.append(sid, {'method':method,'params':params})

    def on_disconnect(self):
        for sid in list(self.turn_images):
            self.cleanup_images(sid)
        for sid in list(self.active):
            self.store.append(sid, {'method':'web/error','params':{'message':'Codex 进程退出；重启服务后可恢复会话'}})
        self.active.clear()
        self.requests.clear()

    def list_sessions(self, archived=False):
        result=[]
        for session in self.store.list(archived=archived):
            try: self.workspaces.resolve(session['cwd'])
            except (ValueError,OSError): continue
            result.append({**session, 'active':session['id'] in self.active, 'attachments':self.attachments.for_session(session['id'])})
        return result

    def checked(self, sid):
        session = self.store.get(sid)
        self.workspaces.resolve(session['cwd'])
        return session

    async def checked_thread(self, sid):
        try:
            return self.checked(sid)
        except KeyError:
            thread = (await self.client.rpc('thread/read', {'threadId':sid,'includeTurns':False}))['thread']
            self.workspaces.resolve(thread['cwd'])
            return thread

    async def native_sessions(self, cursor=None):
        result = await self.client.rpc('thread/list', {'limit':50,'cursor':cursor,'sortKey':'updated_at'})
        visible=[]
        for thread in result['data']:
            try: self.workspaces.resolve(thread['cwd'])
            except (ValueError,OSError): continue
            visible.append({k:thread.get(k) for k in ('id','cwd','name','preview','createdAt','updatedAt')})
        return {'data':visible,'nextCursor':result.get('nextCursor')}

    async def import_session(self, sid, model, effort):
        async with self.operation_lock:
            self.validate_model(model,effort)
            thread = (await self.client.rpc('thread/read', {'threadId':sid,'includeTurns':False}))['thread']
            cwd = self.workspaces.resolve(thread['cwd']).as_posix()
            if sid not in [s['id'] for s in self.store.list()]:
                self.store.save(sid,cwd,model,effort,thread.get('name') or thread.get('preview') or 'Imported Session')
            return await self.resume(sid)

    async def get_session(self, sid):
        self.checked(sid)
        async with self.operation_lock:
            result = await self.resume(sid)
            try:
                snapshot, cursor = await self.client.rpc('thread/read', {'threadId':sid,'includeTurns':True},
                    capture=lambda result: (result, self.store.latest_seq(sid)))
                history, history_events = snapshot['thread'], None
                plans = self.store.turn_plans(sid, cursor)
                for turn in history.get('turns', []):
                    if turn['id'] in plans:
                        turn['webPlan'] = plans[turn['id']]
            except RuntimeError as exc:
                if 'list_turns is not supported yet' not in str(exc):
                    raise
                history, history_events = None, self.store.snapshot_events(sid)
                cursor = self.store.latest_seq(sid)
            token_event = self.store.latest_event(sid, 'thread/tokenUsage/updated')
            token_usage = (token_event.get('params') or {}).get('tokenUsage') if token_event else None
            # Async questions are conversation items, not outstanding RPCs. Restore
            # them after reload, including questions received before this UI supported them.
            resolved = self.store.resolved_requests(sid)
            if history is not None:
                history_items = [item for turn in history.get('turns', []) for item in turn.get('items', [])]
            else:
                history_items = []
                for event in history_events:
                    if event['method'] == 'web/history':
                        history_items = [item for turn in event['params'].get('turns', []) for item in turn.get('items', [])]
                    elif event['method'] == 'item/completed':
                        history_items.append(event['params']['item'])
            for item in history_items:
                req = self.async_question(sid, item)
                if req and req['id'] not in resolved:
                    self.requests.setdefault(req['id'], req)
        state = {'active':sid in self.active,'requests':[v for v in self.requests.values() if v['params'].get('threadId')==sid]}
        return {**result, **state, 'history':history, 'historyEvents':history_events, 'cursor':cursor, 'tokenUsage':token_usage, 'attachments':self.attachments.for_session(sid)}

    async def archive_session(self, sid):
        async with self.operation_lock:
            await self.checked_thread(sid)
            await self.archive(sid)
            self.store.archive(sid)
        return {'ok':True}

    async def rename_session(self, sid, title):
        async with self.operation_lock:
            self.checked(sid)
            self.store.rename(sid, title)
            return self.store.get(sid)

    async def delete_session(self, sid):
        async with self.operation_lock:
            await self.checked_thread(sid)
            await self.delete(sid)
            self.store.delete(sid)
            self.attachments.delete_session(sid)
        return {'ok':True}

    async def send_message(self, sid, message, attachment_ids, model, effort, service_tier='default', sandbox=None):
        self.checked(sid)
        extra = self.attachments.inputs(sid,attachment_ids)
        if not message.strip() and not extra: raise ValueError('消息和附件不能同时为空')
        await self.send(sid,message or '请查看上传的附件。',model,effort,service_tier,extra,sandbox)
        return {'ok':True,'title':self.store.get(sid)['title'],'sandbox':self.store.get(sid).get('sandbox') or self.config['sandbox']}

    async def steer_message(self, sid, message, attachment_ids):
        self.checked(sid)
        extra = self.attachments.inputs(sid,attachment_ids)
        if not message.strip() and not extra: raise ValueError('引导内容和附件不能同时为空')
        await self.steer(sid,message,extra)
        return {'ok':True}
