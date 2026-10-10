"""Real Monaco in Chromium, with local static assets and isolated file fixtures.
No production login, workspace or Codex process is used.
"""
import json
import mimetypes
import os
import re
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None

BASE = Path(__file__).resolve().parents[1]
CSP = re.search(r'Content-Security-Policy.*?= "([^"]+)"', (BASE / 'backend/main.py').read_text())[1]


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, body, status=200, content_type='application/json'):
        raw = json.dumps(body).encode() if content_type == 'application/json' else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Content-Security-Policy', CSP)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/' or url.path.startswith('/static/'):
            file = BASE / 'frontend' / ('index.html' if url.path == '/' else url.path[len('/static/'):])
            return self.respond(file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
        if url.path == '/api/account/limits':
            self.server.limit_reads += 1
            return self.respond(self.server.limits)
        if url.path == '/api/config':
            return self.respond(dict(roots=['/fixture'], sandbox='workspace-write', models=[dict(id='test', efforts=['medium'], default_effort='medium')]))
        if url.path == '/api/sessions':
            return self.respond([])
        if url.path.startswith('/api/sessions/'):
            sid = url.path.split('/')[3]
            return self.respond(dict(id=sid, title=sid, cwd='/other' if sid == 'other' else '/fixture',
                model='test', effort='medium', active=False, requests=[], history={'turns': []}))
        if url.path == '/api/native-sessions':
            return self.respond(dict(data=[], nextCursor=None))
        if url.path == '/api/git/status':
            return self.respond(dict(repository=True, branch='main', detached=False, entries=self.server.git_entries))
        if url.path == '/api/git/diff':
            params = parse_qs(url.query)
            if params['path'][0] == 'binary.bin':
                return self.respond(dict(detail='二进制文件，暂不支持差异对比'), 415)
            return self.respond(dict(path=params['path'][0], group=params['group'][0],
                original='int main() {}\n', modified='int main() { return 0; }\n',
                labels=['最近提交 HEAD', '暂存区'] if params['group'][0] == 'staged' else ['暂存区', '磁盘文件']))
        if url.path == '/api/workspaces':
            path = parse_qs(url.query)['path'][0]
            return self.respond(dict(path=path, parent='/fixture' if path != '/fixture' else None,
                directories=[dict(name='src', path='/fixture/src')] if path == '/fixture' else []))
        if url.path == '/api/editor/index':
            return self.respond(dict(paths=sorted(self.server.files), truncated=False, skipped=0))
        if url.path == '/api/editor/tree':
            path = parse_qs(url.query).get('path', [''])[0]
            entries = [dict(name='src', path='src', directory=True)] if not path else [dict(name=p.split('/')[-1], path=p, directory=False) for p in self.server.files]
            return self.respond(dict(entries=entries, ignored=[]))
        if url.path == '/api/editor/file':
            path = parse_qs(url.query)['path'][0]
            if path not in self.server.files:
                return self.respond(dict(detail='文件或目录已不存在'), 404)
            content, revision = self.server.files[path]
            return self.respond(dict(path=path, content=content, revision=str(revision)))
        return self.respond(dict(detail='missing fixture'), 404)

    def do_PUT(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        content, revision = self.server.files[body['path']]
        if body['revision'] != str(revision):
            return self.respond(dict(detail='磁盘文件已被其他程序修改，未覆盖。'), 409)
        self.server.files[body['path']] = (body['content'], revision + 1)
        return self.respond(dict(path=body['path'], revision=str(revision + 1)))


@unittest.skipIf(sync_playwright is None, 'Optional Playwright dependency is not installed')
class EditorBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, executable_path=os.environ.get('CHROMIUM_EXECUTABLE'))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.playwright.stop()
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        self.server.limit_reads = 0
        self.server.limits = dict(authUser=dict(label='fixture-account', mode='chatgpt'), windows=dict(fiveHour=dict(remainingPercent=68,resetsAt=1900000000),weekly=dict(remainingPercent=42,resetsAt=2000000000)),updatedAt=1800000000,message='')
        self.server.files = {'src/main.cpp': ('int main() {\n    return 0;\n}\n', 1), 'src/task.bb': ('SUMMARY = "test"\n', 1)}
        self.server.git_entries = [dict(path='src/main.cpp', group='staged', status='M'),dict(path='src/main.cpp', group='unstaged', status='M'),dict(path='src/task.bb', group='untracked', status='?')]
        self.errors = []
        self.page = self.browser.new_page(viewport=dict(width=1600, height=960))
        self.page.add_init_script('window.EventSource=class {close(){} addEventListener(){}};')
        self.page.add_init_script("window.addEventListener('DOMContentLoaded',async()=>{window.app=await import('/static/app.js');});")
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('console', lambda msg: self.errors.append(msg.text) if msg.type == 'error' and '404' not in msg.text and '409' not in msg.text and '415' not in msg.text else None)
        self.page.goto('http://127.0.0.1:' + str(self.server.server_port))
        expect(self.page.locator('#app')).to_be_visible()
        self.page.locator('#fileTree summary').click()
        self.page.locator('#fileTree button', has_text='main.cpp').click()
        expect(self.page.locator('#editorTabs [role=tab]')).to_have_text('main.cpp', timeout=20000)
        self.page.evaluate("async()=>{window.testMonaco=(await import('/static/vendor/editor-entry.js')).monaco;}")

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def model(self, suffix='main.cpp'):
        return "testMonaco.editor.getModels().find(m=>m.uri.path.endsWith(" + json.dumps(suffix) + '))'

    def test_account_dropdown_quota_refresh_and_mobile_visibility(self):
        expect(self.page.locator('#sidebar #authUser')).to_have_count(0)
        expect(self.page.locator('#windowTitle #reloadCodex')).to_be_visible()
        self.page.locator('#accountButton').click()
        expect(self.page.locator('#accountPanel')).to_be_visible()
        expect(self.page.locator('#authUser')).to_have_text('fixture-account')
        expect(self.page.locator('#fiveHourRemaining')).to_have_text('剩余 68%')
        expect(self.page.locator('#weeklyRemaining')).to_have_text('剩余 42%')
        self.assertEqual(self.page.locator('#fiveHourProgress').evaluate('el=>el.value'), 68)
        expect(self.page.locator('#weeklyReset')).to_contain_text('重置时间：')
        self.server.limits['windows']['fiveHour']['remainingPercent'] = 0
        self.page.locator('#refreshQuota').click()
        expect(self.page.locator('#fiveHourRemaining')).to_have_text('剩余 0%')
        expect(self.page.locator('#fiveHourProgress')).to_be_visible()
        self.server.limits = dict(authUser=dict(label='fixture-account'),windows={},updatedAt=None,message='额度暂不可用')
        self.page.locator('#refreshQuota').click()
        expect(self.page.locator('#fiveHourRemaining')).to_have_text('暂不可用')
        expect(self.page.locator('#fiveHourProgress')).not_to_be_visible()
        self.assertEqual(self.server.limit_reads, 3)
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#accountPanel')).not_to_be_visible()
        expect(self.page.locator('#accountButton')).to_be_focused()
        self.page.set_viewport_size(dict(width=390,height=844))
        expect(self.page.locator('#accountButton')).to_be_visible()
        expect(self.page.locator('#reloadCodex')).to_be_visible()
        self.page.locator('#accountButton').click()
        expect(self.page.locator('#accountPanel')).to_be_visible()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        bounds=self.page.locator('#accountPanel').bounding_box()
        self.assertGreaterEqual(bounds['x'], 0)
        self.assertLessEqual(bounds['x']+bounds['width'], 390)
        self.page.locator('#message').click()
        expect(self.page.locator('#accountPanel')).not_to_be_visible()

    def test_ask_codex_preserves_draft_and_uses_unsaved_selection(self):
        expect(self.page.locator('#askCodex')).to_be_disabled()
        self.page.locator('#message').fill('请解释这段代码')
        self.page.evaluate('(content)=>'+self.model()+'.setValue(content)', 'first\n  selected line\nlast\n')
        self.page.evaluate("testMonaco.editor.getEditors()[0].setSelection(new testMonaco.Range(2,3,3,1))")
        expect(self.page.locator('#askCodex')).to_be_enabled()
        self.page.evaluate("document.getElementById('workbench').classList.add('chat-hidden')")
        self.page.locator('#askCodex').click()
        expect(self.page.locator('#chat')).to_be_visible()
        expect(self.page.locator('#message')).to_be_focused()
        draft = self.page.locator('#message').input_value()
        self.assertTrue(draft.startswith('请解释这段代码'))
        self.assertIn('工作区：/fixture', draft)
        self.assertIn('src/main.cpp（第 2 行，起始列 3）', draft)
        self.assertIn('尚未保存', draft)
        self.assertIn('selected line', draft)
        self.assertNotIn('last', draft)
        self.assertEqual(self.page.evaluate("sessionStorage.getItem('codexWebDraft:new')"), draft)
        self.assertEqual(self.server.files['src/main.cpp'][1], 1)
        self.assertEqual(self.page.evaluate('app.sessions.current'), None)

    def test_ask_codex_context_menu_action_multiselection_and_fences(self):
        content = '```python\nprint(1)\n```\nlast line'
        self.page.evaluate('(content)=>'+self.model()+'.setValue(content)', content)
        self.page.evaluate("testMonaco.editor.getEditors()[0].setSelections([new testMonaco.Selection(1,1,4,1),new testMonaco.Selection(4,1,4,5)])")
        self.page.evaluate("testMonaco.editor.getEditors()[0].getAction('codex.askSelection').run()")
        draft = self.page.locator('#message').input_value()
        self.assertIn('第 1–3 行', draft)
        self.assertIn('第 4 行', draft)
        self.assertIn('````cpp', draft)
        self.assertTrue(draft.endswith('我的问题：'))
        self.page.evaluate('(draft)=>{const box=document.createElement("div");box.id="selection-preview";document.body.append(box);app.conversation.renderMarkdown(box,draft);}', draft)
        expect(self.page.locator('#selection-preview .code-block')).to_have_count(2)
        self.assertEqual(self.page.locator('#selection-preview code').first.inner_text(), '```python\nprint(1)\n```\n')

    def test_ask_codex_rejects_wrong_session_workspace_and_large_context(self):
        self.page.locator('#message').fill('保留我的草稿')
        self.page.evaluate("async()=>{testMonaco.editor.getEditors()[0].setSelection(new testMonaco.Range(1,1,1,4));await app.sessions.selectSession('other');app.sessions.setWorkspace('/fixture');}")
        self.page.locator('#message').fill('保留我的草稿')
        self.page.locator('#askCodex').click()
        expect(self.page.locator('#error')).to_contain_text('工作区不同')
        expect(self.page.locator('#message')).to_have_value('保留我的草稿')
        self.page.evaluate("app.sessions.clearCurrentSession('新对话');"+self.model()+".setValue('x'.repeat(100001));testMonaco.editor.getEditors()[0].setSelection(new testMonaco.Range(1,1,1,100002))")
        self.page.locator('#askCodex').click()
        expect(self.page.locator('#error')).to_contain_text('超过消息长度上限')
        expect(self.page.locator('#message')).to_have_value('保留我的草稿')

    def test_quick_open_keyboard_lines_reuses_models_and_closes(self):
        original = self.page.evaluate(self.model()+'.id')
        self.page.evaluate('(content)=>'+self.model()+'.setValue(content)', 'int main() {\n  // unsaved\n}\n')
        self.page.keyboard.press('Control+p')
        expect(self.page.locator('#quickOpenDialog')).to_be_visible()
        expect(self.page.locator('#quickOpenInput')).to_be_focused()
        self.page.locator('#quickOpenInput').fill('main.cpp:2:3')
        expect(self.page.locator('.quick-open-result')).to_have_count(1)
        self.page.keyboard.press('Enter')
        expect(self.page.locator('#quickOpenDialog')).not_to_be_visible()
        self.assertEqual(self.page.evaluate(self.model()+'.id'), original)
        self.assertIn('unsaved', self.page.evaluate(self.model()+'.getValue()'))
        self.assertEqual(self.page.evaluate('testMonaco.editor.getEditors()[0].getPosition()'), dict(lineNumber=2, column=3))
        self.page.keyboard.press('Control+p')
        self.page.locator('#quickOpenInput').fill('tskbb')
        expect(self.page.locator('.quick-open-result')).to_contain_text('task.bb')
        self.page.keyboard.press('Enter')
        expect(self.page.locator('#editorPath')).to_have_text('src/task.bb')
        self.page.locator('#quickOpenButton').click()
        expect(self.page.locator('.quick-open-result')).to_have_count(2)
        self.page.keyboard.press('ArrowDown')
        self.assertEqual(self.page.locator('.quick-open-result[aria-selected=true]').count(), 1)
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#quickOpenDialog')).not_to_be_visible()
        self.page.locator('#quickOpenButton').click()
        self.page.locator('#quickOpenInput').fill('missing-file')
        expect(self.page.locator('#quickOpenHint')).to_contain_text('没有匹配')
        self.page.keyboard.press('Enter')
        expect(self.page.locator('#quickOpenDialog')).to_be_visible()
        self.page.keyboard.press('Escape')

    def test_codex_completion_syncs_clean_files_tree_and_git(self):
        model_id = self.page.evaluate(self.model()+'.id')
        self.server.files['src/main.cpp'] = ('// changed by Codex\nint main() {}\n', 2)
        self.server.files['src/new.py'] = ('print("new")\n', 1)
        self.server.git_entries.append(dict(path='src/new.py', group='untracked', status='?'))
        self.page.evaluate("async()=>{await app.sessions.selectSession('fixture-session');app.conversation.handleEvent({method:'turn/completed',params:{turn:{id:'sync-turn',status:'completed'}}});}")
        self.page.wait_for_function("()=>"+self.model()+".getValue().startsWith('// changed by Codex')")
        self.assertEqual(self.page.evaluate(self.model()+'.id'), model_id)
        expect(self.page.locator('#fileTree button', has_text='new.py')).to_be_visible()
        expect(self.page.locator('.git-file')).to_have_count(4)
        expect(self.page.locator('#editorTabs [role=tab]')).to_have_text('main.cpp')

    def test_sync_preserves_drafts_and_handles_deleted_files(self):
        self.page.evaluate(self.model()+".setValue('local unsaved')")
        self.server.files['src/main.cpp'] = ('external content', 2)
        self.page.evaluate("window.codexEditor.syncWorkspace()")
        self.assertEqual(self.page.evaluate(self.model()+'.getValue()'), 'local unsaved')
        expect(self.page.locator('#editorNotice')).to_contain_text('已保留未保存内容')
        self.page.locator('#saveFile').click()
        expect(self.page.locator('#editorNotice')).to_contain_text('未覆盖')
        del self.server.files['src/main.cpp']
        self.page.evaluate("window.codexEditor.syncWorkspace()")
        expect(self.page.locator('#editorNotice')).to_contain_text('已删除')
        self.assertEqual(self.page.evaluate(self.model()+'.getValue()'), 'local unsaved')
        expect(self.page.locator('#fileTree button', has_text='main.cpp')).to_have_count(0)

    def test_reply_links_open_monaco_and_locate_lines(self):
        self.page.evaluate("""async()=>{
            await app.sessions.selectSession('fixture-session');
            app.conversation.handleEvent({method:'item/completed',params:{turnId:'links',item:{id:'answer-links',type:'agentMessage',phase:'final_answer',text:'[源码](/fixture/src/main.cpp:2:5) 与 [配方](src/task.bb#L1) 和 `src/main.cpp:3`，src/main.cpp:2。 [网页](https://example.com/) [越界](../private.txt)'}}});
        }""")
        self.page.locator('#messages a', has_text='源码').click()
        self.assertEqual(self.page.evaluate('testMonaco.editor.getEditors()[0].getPosition()'), dict(lineNumber=2, column=5))
        self.page.locator('#messages a', has_text='配方').click()
        expect(self.page.locator('#editorPath')).to_have_text('src/task.bb')
        self.page.locator('#messages a', has_text='src/main.cpp:3').click()
        self.assertEqual(self.page.evaluate('testMonaco.editor.getEditors()[0].getPosition().lineNumber'), 3)
        expect(self.page.locator('#messages a[data-editor-path]')).to_have_count(4)
        self.assertIsNone(self.page.locator('#messages a', has_text='网页').get_attribute('data-editor-path'))
        self.assertIsNone(self.page.evaluate("app.conversation.workspaceFileReference('../private.txt')"))
        self.assertIsNone(self.page.evaluate("app.conversation.workspaceFileReference('/fixture-other/private.txt')"))
        self.assertIsNone(self.page.evaluate("app.conversation.workspaceFileReference('javascript:alert(1)')"))
        self.assertEqual(len(self.page.context.pages), 1)
        # Streaming updates reuse existing DOM nodes: link destinations must update too.
        self.page.evaluate("app.conversation.renderMarkdown(document.querySelector('[data-turn-id=links] .assistant-markdown'),'[源码](src/main.cpp:1)')")
        self.page.locator('#messages a', has_text='源码').click()
        self.assertEqual(self.page.evaluate('testMonaco.editor.getEditors()[0].getPosition().lineNumber'), 1)

    def test_sync_does_not_overwrite_edits_made_during_fetch(self):
        held = []
        self.page.route('**/api/editor/file?*', lambda route: held.append(route))
        self.page.evaluate("window.syncDone=false;void window.codexEditor.syncWorkspace().then(()=>window.syncDone=true)")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate(self.model()+".setValue('typed while loading')")
        held.pop().fulfill(json=dict(path='src/main.cpp', content='external content', revision='2'))
        self.page.wait_for_function('window.syncDone')
        self.assertEqual(self.page.evaluate(self.model()+'.getValue()'), 'typed while loading')
        expect(self.page.locator('#editorNotice')).to_contain_text('新编辑')
        self.page.unroute('**/api/editor/file?*')

    def test_pending_refresh_cannot_update_another_workspace(self):
        held = []
        self.page.route('**/api/editor/file?*', lambda route: held.append(route))
        self.page.evaluate("window.syncDone=false;void window.codexEditor.syncWorkspace().then(()=>window.syncDone=true)")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        original = self.page.evaluate(self.model()+'.getValue()')
        self.page.evaluate("app.sessions.setWorkspace('/other')")
        held.pop().fulfill(json=dict(path='src/main.cpp', content='stale response', revision='2'))
        self.page.wait_for_function('window.syncDone')
        self.assertEqual(self.page.evaluate(self.model()+'.getValue()'), original)
        expect(self.page.locator('#editorNotice')).not_to_contain_text('src/main.cpp')
        self.page.unroute('**/api/editor/file?*')

    def test_edit_tabs_find_save_undo_and_dispose(self):
        original = self.page.evaluate(self.model()+'.id')
        self.page.evaluate(self.model()+".applyEdits([{range:new testMonaco.Range(2,5,2,5),text:'// edited\\n    '}])")
        expect(self.page.locator('#editorTabs [role=tab]')).to_have_text('● main.cpp')
        self.page.locator('#fileTree button', has_text='task.bb').click()
        expect(self.page.locator('#editorTabs [role=tab]')).to_have_count(2)
        self.assertEqual(self.page.evaluate(self.model('task.bb')+'.getLanguageId()'), 'bitbake')
        self.page.get_by_role('tab', name='● main.cpp', exact=True).click()
        self.assertEqual(self.page.evaluate(self.model()+'.id'), original)
        self.page.keyboard.press('Control+End')
        self.page.keyboard.insert_text('// typed')
        self.assertTrue(self.page.evaluate(self.model()+'.getValue()').endswith('// typed'))
        self.page.get_by_role('tab', name='task.bb', exact=True).click()
        self.page.get_by_role('tab', name='● main.cpp', exact=True).click()
        self.page.keyboard.press('Control+z')
        self.assertFalse(self.page.evaluate(self.model()+'.getValue()').endswith('// typed'))
        self.page.keyboard.press('Control+f')
        expect(self.page.locator('.find-widget')).to_be_visible()
        self.page.keyboard.press('Escape')
        self.page.keyboard.press('Control+s')
        expect(self.page.locator('#editorTabs [role=tab]').first).to_have_text('main.cpp')
        self.assertIn('// edited', self.server.files['src/main.cpp'][0])
        self.page.get_by_role('button', name='关闭 src/task.bb', exact=True).click()
        self.assertEqual(self.page.evaluate('testMonaco.editor.getModels().length'), 1)
        self.page.screenshot(path='/tmp/codex-web-monaco.png')

    def test_conflict_reload_and_workspace_drafts(self):
        self.page.evaluate(self.model()+".setValue('our edit')")
        self.server.files['src/main.cpp'] = ('external edit', 2)
        self.page.locator('#saveFile').click()
        expect(self.page.locator('#editorNotice')).to_contain_text('未覆盖')
        self.assertEqual(self.server.files['src/main.cpp'][0], 'external edit')
        self.page.evaluate("app.sessions.setWorkspace('/other');app.sessions.setWorkspace('/fixture')")
        self.assertEqual(self.page.evaluate(self.model()+'.getValue()'), 'our edit')
        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.locator('#reloadFile').click()
        expect(self.page.locator('#editorTabs [role=tab]')).to_have_text('main.cpp')
        self.assertEqual(self.page.evaluate(self.model()+'.getValue()'), 'external edit')
        self.page.evaluate(self.model()+".setValue('unsaved again')")
        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.locator('#reloadFile').click()
        expect(self.page.locator('#editorTabs [role=tab]')).to_have_text('main.cpp')
        self.assertEqual(self.page.evaluate(self.model()+'.getValue()'), 'external edit')

    def test_cpp_enhanced_highlighting_preserves_literals_and_keywords(self):
        source = '\n'.join([
            'struct sensor { int value; };', 'class Controller {};',
            'enum class Mode { Ready };', 'size_t count = MAX_COUNT;',
            'sensor.value = readValue();', 'device->update();',
            'if (count) return;', '// class Fake { call(); }',
            'const char* s = "struct Fake call()";',
            'auto raw = R"tag(class Fake {', 'call(); })tag";',
            '#include <sensor.h>', 'struct /* comment */', 'lowercase {};'
        ])
        for language in ['c', 'cpp']:
            lines = self.page.evaluate('([source,lang])=>testMonaco.editor.tokenize(source,lang).map((tokens,i)=>tokens.map((t,j)=>({text:source.split("\\n")[i].slice(t.offset,tokens[j+1]?.offset),type:t.type})))', [source, language])
            def kind(line, text):
                return next(t['type'] for t in lines[line] if text in t['text'])
            for line, text in [(0,'sensor'), (1,'Controller'), (2,'Mode'), (3,'size_t'), (13,'lowercase')]:
                self.assertEqual(kind(line,text), 'codex.type.cpp')
            self.assertEqual(kind(3,'MAX_COUNT'), 'codex.macro.cpp')
            self.assertEqual(kind(4,'value'), 'codex.member.cpp')
            self.assertEqual(kind(4,'readValue'), 'codex.function.cpp')
            self.assertEqual(kind(5,'update'), 'codex.function.cpp')
            self.assertTrue(kind(6,'if').startswith('keyword'))
            self.assertTrue(kind(7,'Fake').startswith('comment'))
            self.assertTrue(kind(8,'Fake').startswith('string'))
            self.assertTrue(kind(9,'Fake').startswith('string.raw'))
            self.assertTrue(kind(10,'call').startswith('string.raw'))
            self.assertTrue(kind(11,'sensor.h').startswith('string.include'))

    def test_highlighting_colors_follow_all_themes_without_losing_edits(self):
        source = 'struct Sensor {};\nSensor sensor;\nsensor.value = readValue(MAX_COUNT);'
        self.page.evaluate('(text)=>'+self.model()+'.setValue(text)', source)
        for theme in ['classic-black','tech','white','green','red','yellow','beige']:
            self.page.evaluate('(theme)=>document.documentElement.dataset.theme=theme', theme)
            color = 'rgb(38, 127, 121)' if theme in ['white','beige'] else 'rgb(78, 201, 176)'
            self.page.wait_for_function('color=>Array.from(document.querySelectorAll("#monacoHost .view-line span")).some(el=>el.textContent==="Sensor" && getComputedStyle(el).color===color)', arg=color)
            self.assertEqual(self.page.evaluate(self.model()+'.getValue()'), source)
        self.assertFalse(self.errors)

    def test_language_coverage_resize_and_mobile_chat(self):
        for name, language in [('a.c','c'),('a.h','cpp'),('a.py','python'),('a.sh','shell'),('a.yaml','yaml'),('a.json','json'),('a.md','markdown'),('Makefile','makefile'),('a.bbappend','bitbake'),('a.inc','bitbake'),('a.service','systemd')]:
            path = 'src/' + name
            self.server.files[path] = ('{"key":true}' if language == 'json' else '# test\n', 1)
            self.page.evaluate('path=>codexEditor.openFile(path)', path)
            self.assertEqual(self.page.evaluate(self.model(name)+'.getLanguageId()'), language)
        self.assertTrue(self.page.evaluate("testMonaco.editor.tokenize('{\"key\":true}', 'json')[0].some(t=>t.type.includes('string'))"))
        before = self.page.locator('#codeArea').bounding_box()['width']
        self.page.locator('#chatResize').focus();self.page.keyboard.press('ArrowLeft')
        self.assertLess(self.page.locator('#codeArea').bounding_box()['width'], before)
        self.page.set_viewport_size(dict(width=390, height=844))
        self.assertEqual(self.page.locator('#chat').evaluate('el=>el.clientWidth'), 390)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)

    def test_workbench_views_status_and_theme_on_mobile(self):
        expect(self.page.locator('#editorHeader #layoutControls button')).to_have_count(1)
        expect(self.page.locator('html')).to_have_attribute('data-theme', 'classic-black')
        expect(self.page.locator('#sidebar')).not_to_be_visible()
        expect(self.page.locator('#fileExplorer')).to_be_visible()
        expect(self.page.locator('#editorLanguage')).to_have_text('cpp')
        expect(self.page.locator('#editorEncoding')).to_contain_text('UTF-8')
        self.page.locator('#activitySessions').click()
        expect(self.page.locator('#sidebar')).to_be_visible()
        expect(self.page.locator('#fileExplorer')).not_to_be_visible()
        self.page.locator('#activityFiles').click()
        expect(self.page.locator('#sidebar')).not_to_be_visible()
        expect(self.page.locator('#fileExplorer')).to_be_visible()
        self.page.locator('#activityFiles').click()
        expect(self.page.locator('#fileExplorer')).not_to_be_visible()
        expect(self.page.locator('#monacoHost')).to_be_visible()
        self.page.locator('#activityFiles').click()
        self.page.locator('#message').fill('保留的聊天草稿')
        model_id = self.page.evaluate(self.model()+'.id')
        toggle = self.page.locator('#chatToggle').bounding_box()
        divider = self.page.locator('#chatResize').bounding_box()
        self.assertLessEqual(toggle['x'] + toggle['width'], divider['x'])
        self.assertLess(divider['x'] - toggle['x'] - toggle['width'], 10)
        self.page.locator('#chatToggle').click()
        expect(self.page.locator('#chat .conversation')).not_to_be_visible()
        expect(self.page.locator('#chatToggle')).to_be_visible()
        expect(self.page.locator('#chatToggle')).to_have_attribute('title', '显示Codex 对话')
        expect(self.page.locator('#monacoHost')).to_be_visible()
        self.page.locator('#chatToggle').click()
        expect(self.page.locator('#message')).to_have_value('保留的聊天草稿')
        self.assertEqual(self.page.evaluate(self.model()+'.id'), model_id)
        self.page.locator('#chatToggle').click()
        self.page.locator('#activityChat').click()
        expect(self.page.locator('#chat')).to_be_visible()
        expect(self.page.locator('#message')).to_be_focused()
        self.page.set_viewport_size(dict(width=390, height=844))
        self.page.locator('#themeButton').click()
        expect(self.page.locator('#themeMenu')).to_be_visible()
        self.page.locator('[data-theme-option="tech"]').click()
        expect(self.page.locator('html')).to_have_attribute('data-theme', 'tech')
        self.page.locator('#themeButton').click()
        self.page.locator('[data-theme-option="classic-black"]').click()
        self.page.screenshot(path='/tmp/codex-web-vscode-mobile.png')
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)

    def test_workspace_dropdown_selection_and_dismissal(self):
        self.page.locator('#workspaceButton').click()
        panel = self.page.locator('#directoryDropdown')
        expect(panel).to_be_visible()
        self.assertEqual(self.page.locator('dialog[open]').count(), 0)
        button = self.page.locator('#workspaceButton').bounding_box()
        box = panel.bounding_box()
        self.assertAlmostEqual(box['x'], button['x'], delta=2)
        self.assertGreaterEqual(box['y'], button['y']+button['height'])
        self.page.locator('#directories').get_by_role('button', name='src', exact=True).click()
        expect(self.page.locator('#directoryPath')).to_have_text('/fixture/src')
        self.page.locator('#chooseDirectory').click()
        expect(panel).not_to_be_visible()
        expect(self.page.locator('#workspaceButton')).to_have_text('/fixture/src ▾')
        expect(self.page.locator('#editorWorkspace')).to_have_text('/fixture/src')
        self.page.locator('#workspaceButton').click()
        self.page.keyboard.press('Escape')
        expect(panel).not_to_be_visible()
        expect(self.page.locator('#workspaceButton')).to_be_focused()
        self.page.locator('#workspaceButton').click()
        self.page.locator('#message').click()
        expect(panel).not_to_be_visible()
        self.page.set_viewport_size(dict(width=390, height=844))
        self.page.locator('#workspaceButton').click()
        expect(panel).to_be_visible()
        box = panel.bounding_box()
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x']+box['width'], 390)
        self.page.screenshot(path='/tmp/codex-web-workspace-dropdown.png')

    def test_file_and_session_sidebars_share_width(self):
        def drag(handle, delta):
            box = self.page.locator(handle).bounding_box()
            x, y = box['x'] + box['width']/2, box['y'] + 80
            self.page.mouse.move(x, y)
            self.page.mouse.down()
            self.page.mouse.move(x+delta, y, steps=5)
            self.page.mouse.up()
        tree = self.page.locator('#fileExplorer')
        initial = tree.bounding_box()['width']
        drag('#treeResize', 70)
        tree_width = tree.bounding_box()['width']
        self.assertAlmostEqual(tree_width, initial+70, delta=2)
        self.page.locator('#activitySessions').click()
        sidebar = self.page.locator('#sidebar')
        initial = sidebar.bounding_box()['width']
        self.assertAlmostEqual(initial, tree_width, delta=2)
        drag('#sessionResize', 90)
        session_width = sidebar.bounding_box()['width']
        self.assertAlmostEqual(session_width, initial+90, delta=2)
        drag('#sessionResize', -40)
        self.assertAlmostEqual(sidebar.bounding_box()['width'], session_width-40, delta=2)
        self.page.locator('#activityFiles').click()
        self.assertAlmostEqual(tree.bounding_box()['width'], session_width-40, delta=2)
        self.page.locator('#activitySessions').click()
        self.assertAlmostEqual(sidebar.bounding_box()['width'], session_width-40, delta=2)
        self.page.locator('#sessionResize').focus()
        self.page.keyboard.press('ArrowLeft')
        self.assertAlmostEqual(sidebar.bounding_box()['width'], session_width-60, delta=2)

        self.page.locator('#activityFiles').click()
        self.assertAlmostEqual(tree.bounding_box()['width'], session_width-60, delta=2)

    def test_git_groups_readonly_diff_and_editor_draft_survive(self):
        expect(self.page.locator('#statusBranch')).to_be_visible()
        expect(self.page.locator('#statusBranch')).to_have_text('⑂ main')
        # The staged and unstaged entries for main.cpp count as one file.
        expect(self.page.locator('#gitChangeCount')).to_have_text('2')
        expect(self.page.locator('#gitChangeCount')).to_be_visible()
        expect(self.page.locator('#activityGit')).to_have_attribute('aria-label', '源代码管理，2 个改动文件')
        self.page.evaluate(self.model()+".setValue('unsaved draft')")
        model_id = self.page.evaluate(self.model()+'.id')
        self.page.locator('#activityGit').click()
        expect(self.page.locator('#gitView')).to_be_visible()
        expect(self.page.locator('#filesView')).not_to_be_visible()
        expect(self.page.locator('#gitBranch')).to_contain_text('main')
        expect(self.page.locator('.git-file')).to_have_count(3)
        self.page.locator('.git-file[data-group=staged]').click()
        expect(self.page.locator('#gitDiffPane')).to_be_visible()
        expect(self.page.locator('#gitOriginalLabel')).to_have_text('最近提交 HEAD')
        expect(self.page.locator('#gitModifiedLabel')).to_have_text('暂存区')
        expect(self.page.locator('#saveFile')).to_be_disabled()
        values = self.page.evaluate("testMonaco.editor.getModels().filter(m=>m.uri.scheme==='git-view').map(m=>m.getValue())")
        self.assertEqual(values, ['int main() {}\n', 'int main() { return 0; }\n'])
        self.assertTrue(self.page.evaluate('testMonaco.editor.getDiffEditors()[0].getModifiedEditor().getOption(testMonaco.editor.EditorOption.readOnly)'))
        self.page.locator('.git-file[data-group=unstaged]').click()
        expect(self.page.locator('#gitOriginalLabel')).to_have_text('暂存区')
        self.assertEqual(self.page.evaluate("testMonaco.editor.getModels().filter(m=>m.uri.scheme==='git-view').length"), 2)
        self.page.screenshot(path='/tmp/codex-web-git-diff.png')
        self.page.locator('#closeGitDiff').click()
        self.assertEqual(self.page.evaluate("testMonaco.editor.getModels().filter(m=>m.uri.scheme==='git-view').length"), 0)
        self.assertEqual(self.page.evaluate(self.model()+'.id'), model_id)
        self.assertEqual(self.page.evaluate(self.model()+'.getValue()'), 'unsaved draft')
        self.server.git_entries = []
        self.page.locator('#refreshGit').click()
        expect(self.page.locator('#gitNotice')).to_contain_text('工作区干净')
        expect(self.page.locator('#gitChangeCount')).not_to_be_visible()
        self.page.route('**/api/git/status?*', lambda route: route.fulfill(json=dict(repository=True, branch='feature/status-bar', detached=False, entries=[])))
        self.page.locator('#refreshGit').click()
        expect(self.page.locator('#statusBranch')).to_have_text('⑂ feature/status-bar')
        self.page.unroute('**/api/git/status?*')
        self.page.route('**/api/git/status?*', lambda route: route.fulfill(json=dict(repository=False, entries=[])))
        self.page.locator('#refreshGit').click()
        expect(self.page.locator('#statusBranch')).not_to_be_visible()
        self.page.locator('#activityFiles').click()
        expect(self.page.locator('#filesView')).to_be_visible()

    def test_git_binary_error_and_workspace_switch_release_diff(self):
        self.server.git_entries.append(dict(path='binary.bin', group='untracked', status='?'))
        self.page.locator('#activityGit').click()
        self.page.locator('.git-file[data-path="binary.bin"]').click()
        expect(self.page.locator('#editorNotice')).to_contain_text('二进制')
        self.page.locator('.git-file[data-group=staged]').click()
        expect(self.page.locator('#gitDiffPane')).to_be_visible()
        self.page.evaluate("app.sessions.setWorkspace('/other')")
        expect(self.page.locator('#gitDiffPane')).not_to_be_visible()
        self.assertEqual(self.page.evaluate("testMonaco.editor.getModels().filter(m=>m.uri.scheme==='git-view').length"), 0)
