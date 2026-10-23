import asyncio
import contextlib
import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager
from fastapi import APIRouter, FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal
from .attachments import Attachments, MAX_FILE_BYTES
from .config import BASE, load_config
from .workspace import Workspaces
from .editor_files import EditorFiles, MAX_EDITOR_BYTES
from .git_view import GitView
from .session_manager import SessionStore
from .codex_manager import CodexManager

def create_app(config=None):
    @asynccontextmanager
    async def lifespan(app):
        state = app.state
        state.config = config if config is not None else load_config()
        state.workspaces = Workspaces(state.config['allowed_roots'])
        state.editor_files = EditorFiles(state.workspaces, state.config.get('editor_ignored_names'))
        state.git_view = GitView(state.editor_files)
        state.config['data_dir'].mkdir(mode=0o700, parents=True, exist_ok=True)
        state.store = SessionStore(state.config['data_dir'] / 'sessions.sqlite3')
        codex = None
        auth_watcher = None
        try:
            state.attachments = Attachments(state.config['data_dir'])
            codex = state.codex = CodexManager(state.config, state.store, state.attachments, state.workspaces)
            state.logins, state.attempts = {}, {}
            await codex.start()
            auth_watcher = asyncio.create_task(codex.watch_auth())
            yield
        finally:
            try:
                if auth_watcher:
                    auth_watcher.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await auth_watcher
            finally:
                try:
                    if codex:
                        await codex.close()
                finally:
                    state.store.db.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.middleware('http')(security)
    app.include_router(router)
    app.mount('/static', StaticFiles(directory=BASE/'frontend'), name='static')
    return app


router = APIRouter()

async def security(request: Request, call_next):
    state = request.app.state
    origin = request.headers.get('origin')
    if origin and origin not in state.config['allowed_origins']:
        return JSONResponse({'detail':'不允许的请求来源'}, status_code=403)
    if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('x-codex-web') != '1':
        return JSONResponse({'detail':'缺少请求校验头'}, status_code=403)
    if request.url.path.startswith('/api/') and request.url.path != '/api/login':
        if state.logins.get(request.cookies.get('codex_web'), 0) < time.time():
            return JSONResponse({'detail':'请先登录'}, status_code=401)
    try:
        response = await call_next(request)
    except (ValueError, KeyError, OSError, RuntimeError, asyncio.TimeoutError) as exc:
        response = JSONResponse({'detail':str(exc) or 'Codex 请求超时，请刷新检查会话状态'}, status_code=400)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; worker-src 'self'; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; img-src 'self' blob: data:"
    return response

class Login(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=1000)

@router.post('/api/login')
async def login(body: Login, request: Request):
    state = request.app.state
    now = time.time()
    for key in list(state.logins):
        if state.logins[key] < now: del state.logins[key]
    for key in list(state.attempts):
        state.attempts[key] = [t for t in state.attempts[key] if t > now-60]
        if not state.attempts[key]: del state.attempts[key]
    ip = request.client.host
    if len(state.attempts.get(ip, [])) >= 10:
        raise HTTPException(429, '登录尝试过多，请一分钟后重试')
    state.attempts.setdefault(ip, []).append(now)
    valid_user = hmac.compare_digest(body.username.encode(), str(state.config['username']).encode())
    valid_password = hmac.compare_digest(hashlib.sha256(body.password.encode()).digest(), hashlib.sha256(str(state.config['password']).encode()).digest())
    if not (valid_user and valid_password):
        raise HTTPException(401, '用户名或密码错误')
    state.attempts.pop(ip, None)
    token = secrets.token_urlsafe(32)
    state.logins[token] = now + 43200
    response = JSONResponse({'ok':True})
    response.set_cookie('codex_web',token,httponly=True,samesite='strict',secure=state.config['cookie_secure'],max_age=43200)
    return response

@router.post('/api/logout')
async def logout(request: Request):
    request.app.state.logins.pop(request.cookies.get('codex_web'),None)
    response = JSONResponse({'ok':True})
    response.delete_cookie('codex_web')
    return response

@router.get('/api/config')
async def public_config(request: Request):
    state = request.app.state
    return {'roots':[r.as_posix() for r in state.workspaces.roots], 'models':state.codex.models, 'sandbox':state.config['sandbox'], 'authUser':state.codex.running_auth_user}

@router.get('/api/account/limits')
async def account_limits(request: Request):
    return await request.app.state.codex.account_limits()

@router.post('/api/codex/reload')
async def reload_codex(request: Request):
    return {'models':await request.app.state.codex.restart()}

@router.get('/api/workspaces')
async def browse(request: Request, path: str):
    return request.app.state.workspaces.browse(path)

@router.get('/api/files')
async def open_workspace_file(request: Request, path: str):
    return FileResponse(request.app.state.workspaces.resolve_file(path))

@router.get('/api/editor/tree')
def editor_tree(request: Request, workspace: str, path: str = ''):
    return request.app.state.editor_files.tree(workspace, path)

@router.get('/api/editor/index')
def editor_index(request: Request, workspace: str):
    return request.app.state.editor_files.file_index(workspace)

@router.get('/api/editor/file')
def editor_read(request: Request, workspace: str, path: str):
    return request.app.state.editor_files.read(workspace, path)

@router.get('/api/git/status')
def git_status(request: Request, workspace: str):
    return request.app.state.git_view.status(workspace)

@router.get('/api/git/diff')
def git_diff(request: Request, workspace: str, path: str, group: str):
    return request.app.state.git_view.diff(workspace, path, group)

class EditorSave(BaseModel):
    workspace: str = Field(max_length=4096)
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=MAX_EDITOR_BYTES)
    revision: str = Field(min_length=1, max_length=256)

@router.put('/api/editor/file')
def editor_save(request: Request, body: EditorSave):
    return request.app.state.editor_files.save(body.workspace, body.path, body.content, body.revision)

@router.get('/api/sessions')
async def sessions(request: Request, archived: bool = False):
    return request.app.state.codex.list_sessions(archived)

class NewSession(BaseModel):
    cwd: str
    model: str
    effort: str

@router.post('/api/sessions')
async def create(request: Request, body: NewSession):
    return await request.app.state.codex.create(body.cwd, body.model, body.effort)

@router.get('/api/native-sessions')
async def native_sessions(request: Request, cursor: str = None):
    return await request.app.state.codex.native_sessions(cursor)

class ImportSession(BaseModel):
    model: str
    effort: str

@router.post('/api/sessions/{sid}/import')
async def import_session(request: Request, sid: str, body: ImportSession):
    return await request.app.state.codex.import_session(sid, body.model, body.effort)

@router.get('/api/sessions/{sid}')
async def session(request: Request, sid: str):
    return await request.app.state.codex.get_session(sid)

@router.post('/api/sessions/{sid}/archive')
async def archive_session(request: Request, sid: str):
    return await request.app.state.codex.archive_session(sid)

class SessionPin(BaseModel):
    pinned: bool = Field(strict=True)

@router.post('/api/sessions/{sid}/pin')
async def pin_session(request: Request, sid: str, body: SessionPin):
    return await request.app.state.codex.pin_session(sid, body.pinned)

class SessionName(BaseModel):
    title: str = Field(min_length=1, max_length=120)

@router.patch('/api/sessions/{sid}')
async def rename_session(request: Request, sid: str, body: SessionName):
    return await request.app.state.codex.rename_session(sid, body.title)

@router.delete('/api/sessions/{sid}')
async def delete_session(request: Request, sid: str):
    return await request.app.state.codex.delete_session(sid)

class Message(BaseModel):
    message: str = Field(default='',max_length=100000)
    attachments: list[str] = Field(default_factory=list)
    model: str
    effort: str
    service_tier: str = 'default'
    sandbox: Literal['read-only', 'workspace-write', 'danger-full-access'] | None = None

class SteerMessage(BaseModel):
    message: str = Field(default='',max_length=100000)
    attachments: list[str] = Field(default_factory=list)

@router.post('/api/sessions/{sid}/messages')
async def send(request: Request, sid: str, body: Message):
    return await request.app.state.codex.send_message(sid, body.message, body.attachments, body.model, body.effort, body.service_tier, body.sandbox)

@router.post('/api/sessions/{sid}/steer')
async def steer(request: Request, sid: str, body: SteerMessage):
    return await request.app.state.codex.steer_message(sid, body.message, body.attachments)

@router.post('/api/sessions/{sid}/attachments')
async def upload_attachment(sid: str, request: Request, name: str):
    request.app.state.codex.checked(sid)
    if int(request.headers.get('content-length','0')) > MAX_FILE_BYTES:
        raise HTTPException(413, '单个附件不能超过 20 MB')
    return await request.app.state.attachments.upload(sid, name, request.stream())

@router.get('/api/sessions/{sid}/attachments')
async def list_attachments(request: Request, sid: str):
    request.app.state.codex.checked(sid)
    return request.app.state.attachments.for_session(sid)

@router.get('/api/sessions/{sid}/attachments/{aid}')
async def download_attachment(request: Request, sid: str, aid: str):
    request.app.state.codex.checked(sid)
    meta, path = request.app.state.attachments.get(sid, aid)
    return FileResponse(path, filename=meta['name'], media_type='application/octet-stream')

@router.post('/api/sessions/{sid}/stop')
async def stop(request: Request, sid: str):
    request.app.state.codex.checked(sid)
    await request.app.state.codex.stop(sid)
    return {'ok':True}

@router.post('/api/sessions/{sid}/requests/{rid}')
async def answer(request: Request, sid: str, rid: str, body: dict):
    request.app.state.codex.checked(sid)
    await request.app.state.codex.answer(sid,rid,body)
    return {'ok':True}

@router.get('/api/sessions/{sid}/events')
async def events(sid: str, request: Request, after: int = 0):
    state = request.app.state
    state.codex.checked(sid)
    cursor = max(after, int(request.headers.get('last-event-id','0')))
    async def stream():
        nonlocal cursor
        heartbeat=0
        while not await request.is_disconnected():
            if state.logins.get(request.cookies.get('codex_web'),0) < time.time():
                yield 'event: expired\ndata: {}\n\n'
                return
            batch=state.store.events(sid,cursor)
            for event in batch:
                cursor=event['seq']
                yield f'id: {cursor}\ndata: {json.dumps(event,ensure_ascii=False)}\n\n'
            heartbeat+=1
            if heartbeat%40==0: yield ': keepalive\n\n'
            await asyncio.sleep(0.1 if batch else 0.25)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

@router.get('/')
async def index(request: Request):
    return FileResponse(BASE/'frontend/index.html')

app = create_app()
