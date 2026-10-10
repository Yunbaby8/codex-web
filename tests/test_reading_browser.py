"""Offline browser regression tests. All HTTP and model events are fixtures.

Requires Playwright; CHROMIUM_EXECUTABLE can select an installed browser.
No login, production database or real model is used.
"""
import os
import time
import mimetypes
import unittest
from pathlib import Path
from urllib.parse import urlparse

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None

FRONTEND = Path(__file__).resolve().parents[1] / 'frontend'
ANSWER = '\n\n'.join(f'第{i}段：阅读期间保持文字与位置稳定。' for i in range(50))
ANSWER += '\n\n```text\n' + '\n'.join(f'line {i} ' + 'x' * 160 for i in range(60)) + '\n```'


@unittest.skipIf(sync_playwright is None, 'Optional Playwright dependency is not installed')
class ReadingBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            headless=True, executable_path=os.environ.get('CHROMIUM_EXECUTABLE'))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.sessions = {sid: self.session(sid) for sid in ('A', 'B')}
        self.delayed_messages = []
        self.errors = []
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 900})
        self.context.add_init_script('''localStorage.setItem('codexWebWorkbenchSidebarCollapsed','false');window.fixtureStreams=[];window.EventSource=class {
            constructor(url){this.url=url;this.closed=false;fixtureStreams.push(this);}
            close(){this.closed=true;} addEventListener(){}
        };''')
        self.page = self.context.new_page()
        self.page.add_init_script("window.addEventListener('DOMContentLoaded',async()=>{window.app=await import('/static/app.js');});")
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.route('**/*', self.route)
        self.page.goto('http://codex-web.test/')
        expect(self.page.locator('#app')).to_be_visible()
        self.page.evaluate("app.sessions.selectSession('A')")

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def session(self, sid):
        return dict(id=sid, title=f'项目 {sid}', cwd='/workspace', model='test', effort='medium',
                    active=False, requests=[], history={'turns': [dict(id='t-' + sid,
                    status='completed', items=[dict(id='a-' + sid, type='agentMessage',
                    phase='final_answer', text=ANSWER)])]})

    def route(self, route):
        request = route.request
        path = urlparse(request.url).path
        if path == '/':
            return route.fulfill(body=(FRONTEND / 'index.html').read_text(), content_type='text/html')
        if path.startswith('/static/'):
            return route.fulfill(body=(FRONTEND / path.split('/')[-1]).read_text(),
                                 content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
        if path.endswith('/messages') or path.endswith('/steer'):
            self.delayed_messages.append(route)
            return
        if path == '/api/git/status':
            data = dict(repository=False, entries=[])
        elif path == '/api/editor/tree':
            data = dict(entries=[], ignored=[])
        elif path == '/api/config':
            data = dict(roots=['/workspace'], sandbox='workspace-write',
                        models=[dict(id='test', efforts=['medium'], default_effort='medium')])
        elif path == '/api/sessions':
            if request.method == 'POST':
                data = self.session('new');self.sessions['new'] = data
            else:
                data = list(self.sessions.values())
        elif path == '/api/native-sessions':
            data = dict(data=[], nextCursor=None)
        elif path.startswith('/api/sessions/'):
            data = self.sessions[path.split('/')[3]]
            if request.method == 'PATCH':
                data['title'] = request.post_data_json['title']
        else:
            self.fail('Unexpected request: ' + path)
        route.fulfill(json=data)

    def take_message(self):
        # click() may finish before the asynchronous fetch reaches the route handler.
        deadline = time.monotonic() + 5
        while not self.delayed_messages and time.monotonic() < deadline:
            self.page.wait_for_timeout(10)
        self.assertTrue(self.delayed_messages, 'Message request did not arrive')
        return self.delayed_messages.pop()

    def delta(self, text, item='a-A', turn='t-A'):
        self.page.evaluate("p=>app.conversation.handleEvent({method:'item/agentMessage/delta',params:p})",
                           dict(itemId=item, turnId=turn, delta=text))

    def test_markdown_list_numbering_and_item_paragraphs(self):
        cases = [
            ('1. 第一项\n2. 第二项\n3. 第三项', [1], [3]),
            ('1. 第一项\n\n2. 第二项\n\n3. 第三项', [1], [3]),
            ('1. 第一项\n\n1. 第二项\n\n1. 第三项', [1], [3]),
            ('3. 第三项\n4. 第四项', [3], [2]),
            ('1. 第一项\n\n独立段落\n\n3. 第三项', [1, 3], [1, 1]),
        ]
        for text, starts, lengths in cases:
            with self.subTest(text=text):
                result = self.page.evaluate('''text=>{
                    const box=document.createElement('div');app.conversation.renderMarkdown(box,text);
                    return {starts:[...box.querySelectorAll('ol')].map(ol=>ol.start),
                        lengths:[...box.querySelectorAll('ol')].map(ol=>ol.children.length)};
                }''', text)
                self.assertEqual(result, dict(starts=starts, lengths=lengths))
        result = self.page.evaluate('''()=>{
            const box=document.createElement('div');
            app.conversation.renderMarkdown(box,'1. 第一项\\n\\n   补充说明\\n\\n2. 第二项\\n\\n   - 子项\\n\\n3. 第三项');
            return {lists:box.querySelectorAll(':scope > ol').length,
                items:box.querySelectorAll(':scope > ol > li').length,
                paragraph:box.querySelector('ol > li > p:nth-child(2)').textContent,
                nested:box.querySelector('ol > li > ul > li').textContent};
        }''')
        self.assertEqual(result, dict(lists=1, items=3, paragraph='补充说明', nested='子项'))

    def test_markdown_list_numbering_survives_streaming(self):
        result = self.page.evaluate('''()=>{
            const box=document.createElement('div');
            app.conversation.renderMarkdown(box,'3. 第三项');const list=box.firstChild;
            app.conversation.renderMarkdown(box,'3. 第三项\\n\\n4. 第四项');
            const extended={same:box.firstChild===list,start:list.start,items:list.children.length};
            app.conversation.renderMarkdown(box,'1. 第一项\\n\\n2. 第二项');
            return {extended,start:box.firstChild.start,items:box.firstChild.children.length};
        }''')
        self.assertEqual(result, dict(extended=dict(same=True, start=3, items=2), start=1, items=2))

    def test_session_switch_ignores_old_stream_and_updates_composer(self):
        self.page.evaluate("window.oldStream=fixtureStreams.at(-1)")
        self.page.evaluate("app.sessions.selectSession('B')")
        self.assertTrue(self.page.evaluate('oldStream.closed'))
        self.page.evaluate("oldStream.onmessage({data:JSON.stringify({method:'turn/started',params:{turn:{id:'stale'}}})})")
        expect(self.page.locator('[data-turn-id=stale]')).to_have_count(0)
        expect(self.page.locator('#stop')).to_be_hidden()
        self.page.evaluate("fixtureStreams.at(-1).onmessage({data:JSON.stringify({method:'turn/started',params:{turn:{id:'live'}}})})")
        expect(self.page.locator('#stop')).to_be_visible()
        expect(self.page.locator('#send')).to_have_attribute('aria-label', '引导当前任务')
        self.assertEqual(self.page.evaluate('app.sessions.current.id'), 'B')

    def test_late_session_response_does_not_replace_newer_selection(self):
        held = []
        self.page.route('**/api/sessions/A', lambda route: held.append(route))
        self.page.evaluate("window.oldSelection=app.sessions.selectSession('A');void 0")
        self.page.evaluate("app.sessions.selectSession('B')")
        self.assertEqual(len(held), 1)
        held[0].fulfill(json=self.sessions['A'])
        self.page.evaluate('oldSelection')
        self.assertEqual(self.page.evaluate('app.sessions.current.id'), 'B')
        expect(self.page.locator('#chatTitle')).to_have_text('项目 B')
        expect(self.page.locator('[data-turn-id=t-A]')).to_have_count(0)

    def test_expired_login_from_module_request_closes_stream(self):
        self.page.route('**/api/sessions/B', lambda route: route.fulfill(status=401, json={'detail':'请先登录'}))
        message = self.page.evaluate("app.sessions.selectSession('B').catch(error=>error.message)")
        self.assertEqual(message, '请先登录')
        expect(self.page.locator('#loginPanel')).to_be_visible()
        expect(self.page.locator('#app')).to_be_hidden()
        self.assertTrue(self.page.evaluate('fixtureStreams.at(-1).closed'))

    def test_rerun_uses_original_message_and_preserves_composer_draft(self):
        self.page.evaluate("""()=>{
            app.conversation.handleEvent({method:'item/completed',params:{turnId:'rerun',item:{
                id:'question',type:'userMessage',content:[{type:'text',text:'原始问题'}]}}});
            app.conversation.handleEvent({method:'item/completed',params:{turnId:'rerun',item:{
                id:'answer',type:'agentMessage',text:'已有回答'}}});
        }""")
        self.page.locator('#message').fill('下一条草稿')
        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.locator('[data-turn-id=rerun] button[title="再次运行"]').click()
        request = self.take_message()
        self.assertEqual(request.request.post_data_json['message'], '原始问题')
        self.assertEqual(request.request.post_data_json['attachments'], [])
        self.assertTrue(self.page.evaluate('app.composer.submitting'))
        rejection = self.page.evaluate("app.sessions.selectSession('B').catch(error=>error.message)")
        self.assertIn('消息正在发送', rejection)
        request.fulfill(json={'ok': True})
        self.page.wait_for_function('!app.composer.submitting')
        expect(self.page.locator('#message')).to_have_value('下一条草稿')

    def ask_question(self, rid='question-1'):
        self.page.evaluate("p=>app.conversation.handleEvent({method:'web/request',params:p})", dict(
            id=rid, method='item/tool/requestUserInput', params=dict(questions=[
                dict(id='layout', header='布局', question='如何展示？', options=[
                    dict(label='弹出窗口', description='集中显示问题和选项'),
                    dict(label='嵌入页面', description='保持原来的布局')]),
                dict(id='note', question='补充要求', options=[])])))

    def test_question_dialog_options_custom_answer_and_submit(self):
        replies = []
        def answer(route):
            replies.append(route.request.post_data_json)
            route.fulfill(json={'ok': True})
        self.page.route('**/requests/*', answer)
        self.ask_question()
        expect(self.page.locator('#questionDialog')).to_be_visible()
        expect(self.page.get_by_text('集中显示问题和选项')).to_be_visible()
        self.page.locator('#submitQuestion').click()
        expect(self.page.locator('#questionError')).to_have_text('请回答每个问题后再提交。')
        self.assertEqual(replies, [])
        self.page.get_by_label('弹出窗口').check()
        self.page.get_by_label('你的回答', exact=True).fill('保持简洁')
        self.page.locator('#submitQuestion').click()
        expect(self.page.locator('#questionDialog')).not_to_be_visible()
        self.assertEqual(replies, [{'answers': {'layout': '弹出窗口', 'note': '保持简洁'}}])

    def test_question_panel_stays_above_composer_without_blocking_chat(self):
        self.ask_question()
        for width,height in [(1440,900),(390,844)]:
            with self.subTest(width=width):
                self.page.set_viewport_size({'width':width,'height':height})
                geometry=self.page.evaluate('''()=>{
                    const panel=document.getElementById('questionDialog');
                    const rect=id=>{const r=document.getElementById(id).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom};};
                    return {panel:rect('questionDialog'),chat:rect('chat'),composer:rect('composer'),
                        modal:panel.matches(':modal'),parent:panel.parentElement.id};
                }''')
                self.assertEqual(geometry['parent'],'chat')
                self.assertFalse(geometry['modal'])
                panel,chat,composer=(geometry[k] for k in ('panel','chat','composer'))
                self.assertGreaterEqual(panel['left'],chat['left'])
                self.assertLessEqual(panel['right'],chat['right'])
                self.assertGreaterEqual(panel['top'],chat['top'])
                self.assertLessEqual(panel['bottom'],composer['top'])
                self.assertAlmostEqual(panel['left'],composer['left'],delta=1)
                self.assertAlmostEqual(panel['right'],composer['right'],delta=1)
                expect(self.page.get_by_role('button',name='打开提问')).not_to_be_visible()
                self.page.locator('#message').fill('仍然可以编辑消息')
                expect(self.page.locator('#message')).to_be_focused()
                expect(self.page.locator('#questionDialog')).to_be_visible()
                submit=self.page.locator('#submitQuestion').bounding_box()
                self.assertLessEqual(submit['y']+submit['height'],panel['bottom'])
        self.page.locator('#closeQuestion').click()
        expect(self.page.get_by_role('button',name='打开提问')).to_be_visible()

    def test_async_question_survives_turn_end_and_session_restore(self):
        request=dict(id='async:question',method='web/asyncUserInput',params=dict(threadId='A',questions=[
            dict(id='0',question='回答方式？',options=[dict(label='简洁回答'),dict(label='详细回答')])]))
        self.sessions['A']['requests']=[request]
        self.page.evaluate("p=>app.conversation.handleEvent({method:'web/request',params:p})",request)
        self.page.evaluate("app.conversation.handleEvent({method:'turn/completed',params:{turn:{id:'t-A',status:'completed'}}})")
        expect(self.page.locator('#questionDialog')).to_be_visible()
        self.page.keyboard.press('Escape')
        self.page.evaluate("app.sessions.selectSession('B')")
        self.page.evaluate("app.sessions.selectSession('A')")
        expect(self.page.locator('#questionDialog')).to_be_visible()
        replies=[]
        def answer(route):
            replies.append(route.request.post_data_json)
            self.sessions['A']['requests']=[]
            route.fulfill(json={'ok':True})
        self.page.route('**/requests/*',answer)
        self.page.get_by_label('简洁回答',exact=True).check()
        self.page.locator('#submitQuestion').click()
        expect(self.page.locator('#questionDialog')).not_to_be_visible()
        self.assertEqual(replies,[{'answers':{'0':'简洁回答'}}])
        self.page.evaluate("app.sessions.selectSession('A')")
        expect(self.page.locator('#questionDialog')).not_to_be_visible()

    def test_question_dialog_keeps_draft_on_close_rerender_and_failure(self):
        self.page.route('**/requests/*', lambda route: route.fulfill(
            status=500, json={'detail': '暂时无法提交'}))
        self.ask_question()
        custom = self.page.get_by_label('其他（自行填写）', exact=True)
        self.page.get_by_label('弹出窗口').check()
        custom.fill('自定义布局')
        expect(self.page.get_by_label('弹出窗口')).not_to_be_checked()
        self.page.get_by_label('你的回答', exact=True).fill('保留我的答案')
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#questionDialog')).not_to_be_visible()
        self.ask_question()
        expect(self.page.locator('#questionDialog')).not_to_be_visible()
        self.page.get_by_role('button', name='打开提问').click()
        expect(custom).to_have_value('自定义布局')
        self.page.locator('#submitQuestion').click()
        expect(self.page.locator('#questionError')).to_have_text('暂时无法提交')
        expect(custom).to_have_value('自定义布局')
        expect(self.page.locator('#submitQuestion')).to_be_enabled()
        self.page.get_by_label('嵌入页面').check()
        expect(custom).to_have_value('')

    def test_question_dialog_queue_resolution_and_session_switch(self):
        self.ask_question()
        self.page.get_by_label('你的回答', exact=True).fill('尚未提交')
        self.ask_question('question-2')
        expect(self.page.get_by_label('你的回答', exact=True)).to_have_value('尚未提交')
        self.page.evaluate("app.conversation.handleEvent({method:'web/requestResolved',params:{id:'question-1'}})")
        expect(self.page.locator('#questionDialog')).to_have_attribute('data-request', 'question-2')
        expect(self.page.get_by_label('你的回答', exact=True)).to_have_value('')
        self.page.evaluate("app.sessions.selectSession('B')")
        expect(self.page.locator('#questionDialog')).not_to_be_visible()
        self.page.evaluate("app.sessions.selectSession('A')")
        self.ask_question()
        self.page.evaluate("app.conversation.handleEvent({method:'turn/completed',params:{turn:{id:'t-A',status:'completed'}}})")
        expect(self.page.locator('#questionDialog')).not_to_be_visible()

    def test_stream_preserves_selection_code_scroll_and_live_copy(self):
        self.page.evaluate('''()=>{
            const body=document.querySelector('[data-turn-id=t-A] .assistant-markdown'), pre=body.querySelector('pre');
            pre.scrollTop=130;pre.scrollLeft=180;window.originalParagraph=body.querySelector('p');
            const range=document.createRange();range.selectNodeContents(originalParagraph);
            getSelection().removeAllRanges();getSelection().addRange(range);
        }''')
        selected = self.page.evaluate('getSelection().toString()')
        self.delta('\n\n新增的内容')
        result = self.page.evaluate('''()=>{
            const body=document.querySelector('[data-turn-id=t-A] .assistant-markdown'), pre=body.querySelector('pre');
            return {selected:getSelection().toString(),top:pre.scrollTop,left:pre.scrollLeft,
                    same:body.querySelector('p')===originalParagraph};
        }''')
        self.assertEqual(result, dict(selected=selected, top=130, left=180, same=True))
        self.page.evaluate("app.conversation.handleEvent({method:'item/started',params:{turnId:'stream',item:{id:'code',type:'agentMessage',text:'```text\\nfirst'}}})")
        self.delta('\nsecond\n```', 'code', 'stream')
        copied = self.page.evaluate('''async()=>{
            let result;Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{result=text;}}});
            await document.querySelector('[data-turn-id=stream] .assistant-markdown').querySelector('button').onclick({currentTarget:document.querySelector('[data-turn-id=stream] .assistant-markdown').querySelector('button')});
            return result;
        }''')
        self.assertEqual(copied, 'first\nsecond')

    def test_pause_follow_and_restore_position_on_switch_and_reload(self):
        self.page.evaluate("document.getElementById('messages').scrollTop=document.getElementById('messages').scrollHeight-document.getElementById('messages').clientHeight-80")
        self.page.wait_for_function("!document.getElementById('latestMessage').hidden")
        before = self.page.locator('#messages').evaluate('(el)=>el.scrollTop')
        self.delta('\n\n新增内容')
        self.assertEqual(self.page.locator('#messages').evaluate('(el)=>el.scrollTop'), before)
        expect(self.page.locator('#latestMessage')).to_be_visible()
        self.page.locator('#latestMessage').click()
        self.assertLessEqual(self.page.locator('#messages').evaluate('(el)=>el.scrollHeight-el.clientHeight-el.scrollTop'), 2)
        self.page.evaluate("document.getElementById('messages').scrollTop=340")
        self.page.wait_for_function("!document.getElementById('latestMessage').hidden")
        self.page.evaluate("app.sessions.selectSession('B')")
        self.page.evaluate("app.sessions.selectSession('A')")
        self.assertEqual(self.page.locator('#messages').evaluate('(el)=>el.scrollTop'), 340)
        self.page.reload()
        self.page.wait_for_function("app?.sessions.current?.id === 'A' && !app.sessions.selecting")
        self.assertEqual(self.page.locator('#messages').evaluate('(el)=>el.scrollTop'), 340)

    def test_new_draft_survives_send_ack_failure_and_session_switch(self):
        self.page.locator('#message').fill('第一条消息')
        self.page.locator('#send').click()
        self.page.locator('#message').fill('等待期间新写的草稿')
        self.take_message().fulfill(json={'ok': True, 'title': '项目 A'})
        self.page.wait_for_function('!app.composer.submitting')
        expect(self.page.locator('#message')).to_have_value('等待期间新写的草稿')
        self.page.evaluate("app.sessions.selectSession('B')")
        expect(self.page.locator('#message')).to_have_value('')
        self.page.locator('#message').fill('B 的草稿')
        self.page.evaluate("app.sessions.selectSession('A')")
        expect(self.page.locator('#message')).to_have_value('等待期间新写的草稿')
        self.page.locator('#send').click()
        self.take_message().fulfill(status=503, json={'detail': '测试发送失败'})
        self.page.wait_for_function('!app.composer.submitting')
        expect(self.page.locator('#message')).to_have_value('等待期间新写的草稿')
        self.page.reload()
        expect(self.page.locator('#message')).to_have_value('等待期间新写的草稿')
        self.page.locator('#message').fill('\n'.join(['长草稿'] * 15))
        self.assertEqual(self.page.locator('#message').evaluate('el=>el.clientHeight'), 240)

    def test_activity_collapse_respects_reader_and_explicit_choice(self):
        self.page.evaluate('''()=>{
            app.conversation.handleEvent({method:'turn/started',params:{turn:{id:'work'}}});
            app.conversation.handleEvent({method:'item/started',params:{turnId:'work',item:{id:'progress',type:'agentMessage',phase:'commentary',text:'正在读取文件'}}});
        }''')
        self.assertTrue(self.page.evaluate("document.querySelector('[data-turn-id=work] .turn-activity').open"))
        self.page.evaluate("app.conversation.handleEvent({method:'turn/completed',params:{turn:{id:'work',status:'completed'}}})")
        self.assertFalse(self.page.evaluate("document.querySelector('[data-turn-id=work] .turn-activity').open"))
        self.page.evaluate("app.conversation.handleEvent({method:'turn/started',params:{turn:{id:'work'}}})")
        self.page.evaluate("document.getElementById('messages').scrollTop=200")
        self.page.wait_for_function("!document.getElementById('latestMessage').hidden")
        self.page.evaluate("app.conversation.handleEvent({method:'turn/completed',params:{turn:{id:'work',status:'completed'}}})")
        self.assertTrue(self.page.evaluate("document.querySelector('[data-turn-id=work] .turn-activity').open"))
        self.page.evaluate("app.conversation.handleEvent({method:'turn/started',params:{turn:{id:'work'}}})")
        self.page.locator('.turn-activity > summary').last.click()
        self.assertFalse(self.page.evaluate("document.querySelector('[data-turn-id=work] .turn-activity').open"))
        self.page.evaluate("app.conversation.handleEvent({method:'turn/completed',params:{turn:{id:'work',status:'failed',error:{message:'测试错误'}}}})")
        self.assertTrue(self.page.evaluate("document.querySelector('[data-turn-id=work] .turn-activity').open"))
        expect(self.page.locator('#error')).to_contain_text('测试错误')

    def test_first_send_and_steering_keep_new_drafts(self):
        self.page.evaluate("app.sessions.clearCurrentSession('新对话')")
        self.page.locator('#message').fill('首次发送')
        self.page.locator('#send').click()
        self.page.wait_for_function("app.sessions.current?.id === 'new' && !app.sessions.selecting")
        self.page.locator('#message').fill('创建会话期间写的新草稿')
        self.take_message().fulfill(json={'ok': True, 'title': '首次发送'})
        self.page.wait_for_function('!app.composer.submitting')
        expect(self.page.locator('#message')).to_have_value('创建会话期间写的新草稿')
        self.page.evaluate("app.conversation.handleEvent({method:'turn/started',params:{turn:{id:'running'}}})")
        self.page.locator('#message').fill('补充指令')
        self.page.locator('#send').click()
        self.page.locator('#message').fill('尚未发送的另一条指令')
        pending = self.take_message()
        self.assertTrue(pending.request.url.endswith('/steer'))
        pending.fulfill(json={'ok': True})
        self.page.wait_for_function('!app.composer.submitting')
        expect(self.page.locator('#message')).to_have_value('尚未发送的另一条指令')

    def test_steering_preserves_chronology_live_and_after_restore(self):
        def user(item_id, text, **extra):
            return dict(id=item_id, type='userMessage', content=[dict(type='text', text=text)], **extra)

        first = user('u1', '首次要求')
        steer = user('u2', '补充要求', webSteer=True)
        next_steer = user('u3', '再次补充', webSteer=True)
        before = dict(id='r1', type='reasoning', summary=['之前的思考'], content=[])
        after = dict(id='r2', type='reasoning', summary=['引导后的思考'], content=[])
        progress = dict(id='p3', type='agentMessage', phase='commentary', text='继续处理最新要求')
        file1 = dict(id='f1', type='fileChange', status='inProgress', changes=[
            dict(path='/workspace/before.py', diff='@@ -1 +1 @@\n-old\n+before')])
        file2 = dict(id='f2', type='fileChange', status='completed', changes=[
            dict(path='/workspace/after.py', diff='@@ -1 +1 @@\n-old\n+after')])
        events = [dict(method='turn/started', params=dict(turn=dict(id='flow', status='inProgress')))]
        for item in [first, before, file1, steer, after, file2, next_steer, progress]:
            method = 'web/steer' if item.get('webSteer') else 'item/started'
            events.append(dict(method=method, params=dict(turnId='flow', item=item)))
        # Completion of an earlier operation must update its original segment.
        events.append(dict(method='item/completed', params=dict(turnId='flow', item=dict(file1, status='completed'))))
        events.append(dict(method='item/completed', params=dict(turnId='flow', item=dict(before, summary=['之前的思考已完成']))))
        self.page.evaluate("events=>{app.conversation.reset();for(const e of events)app.conversation.handleEvent(e);}", events)

        def check_order(running=True):
            turn = self.page.locator('[data-turn-id=flow]')
            expect(turn.locator('.user')).to_have_count(3)
            expect(turn.locator('.turn-activity')).to_have_count(3)
            expected = ['首次要求', '之前的思考已完成', '补充要求', '引导后的思考', '再次补充', '继续处理最新要求']
            text = turn.text_content()
            positions = [text.index(part) for part in expected]
            self.assertEqual(positions, sorted(positions))
            expect(turn.locator('.turn-activity').nth(0)).to_contain_text('before.py')
            expect(turn.locator('.turn-activity').nth(0)).not_to_contain_text('after.py')
            expect(turn.locator('.turn-activity').nth(1)).to_contain_text('after.py')
            expect(turn.locator('.turn-activity').nth(1)).not_to_contain_text('before.py')
            if running:
                expect(turn.locator('.turn-thinking')).to_be_visible()
            else:
                expect(turn.locator('.turn-thinking')).to_be_hidden()

        check_order()
        # Replay the Web journal, then restore the native thread snapshot (which
        # has ordinary userMessage items instead of the Web-only steer marker).
        self.page.evaluate("s=>app.conversation.restoreSession(s)", dict(id='A', active=True, requests=[], historyEvents=events))
        check_order()
        native_items = [first, dict(before, summary=['之前的思考已完成']), dict(file1, status='completed'),
                        user('u2', '补充要求'), after, file2, user('u3', '再次补充'), progress]
        session = dict(id='A', active=True, requests=[], history=dict(turns=[dict(id='flow', status='inProgress', items=native_items)]))
        self.page.evaluate("s=>app.conversation.restoreSession(s)", session)
        check_order()
        # Each section keeps its own manual collapse choice through a reload.
        sections = self.page.locator('[data-turn-id=flow] .turn-activity')
        sections.nth(1).locator(':scope > summary').click()
        self.page.evaluate('app.conversation.saveReadingView()')
        self.page.evaluate("s=>app.conversation.restoreSession(s)", session)
        self.assertEqual(sections.evaluate_all('nodes=>nodes.map(n=>n.open)'), [True, False, True])
        self.page.evaluate("app.conversation.handleEvent({method:'turn/completed',params:{turn:{id:'flow',status:'completed'}}})")
        expect(self.page.locator('[data-turn-id=flow] .turn-thinking')).to_be_hidden()
        session['active'] = False
        session['history']['turns'][0]['status'] = 'completed'
        self.page.evaluate("s=>app.conversation.restoreSession(s)", session)
        check_order(running=False)

    def test_steering_before_reasoning_summary_keeps_previous_process_visible(self):
        events = [dict(method='turn/started', params=dict(turn=dict(id='empty-flow', status='inProgress')))]
        for index in range(3):
            item = dict(id=f'empty-u{index}', type='userMessage', content=[dict(type='text', text=f'指令{index}')])
            events.append(dict(method='item/completed', params=dict(turnId='empty-flow', item=item)))
        events.insert(2, dict(method='item/started', params=dict(turnId='empty-flow',
            item=dict(id='empty-reasoning', type='reasoning', summary=[], content=[]))))
        self.page.evaluate("events=>{for(const e of events)app.conversation.handleEvent(e);}", events)
        turn = self.page.locator('[data-turn-id=empty-flow]')
        expect(turn.locator('.turn-activity:visible')).to_have_count(2)
        expect(turn.locator('.turn-thinking')).to_be_visible()
        order = turn.evaluate("node=>[...node.children].filter(n=>!n.hidden).map(n=>n.className)")
        self.assertEqual(order, ['turn-user', 'turn-activity', 'turn-steers', 'turn-activity', 'turn-steers', 'turn-thinking'])
        self.page.evaluate("app.conversation.handleEvent({method:'item/reasoning/summaryTextDelta',params:{turnId:'empty-flow',itemId:'empty-reasoning',summaryIndex:0,delta:'迟到的摘要'}})")
        expect(turn.locator('.turn-activity').nth(0).locator('.reasoning-entry')).to_contain_text('迟到的摘要')
        expect(turn.locator('.turn-activity').nth(0).locator('.activity-placeholder')).to_be_hidden()

    def test_attachment_drafts_belong_to_each_session(self):
        self.page.locator('#fileInput').set_input_files(dict(name='note.txt', mimeType='text/plain', buffer=b'fixture'))
        expect(self.page.locator('#attachments')).to_contain_text('note.txt')
        self.page.evaluate("app.sessions.selectSession('B')")
        expect(self.page.locator('#attachments .attachment')).to_have_count(0)
        self.page.evaluate("app.sessions.selectSession('A')")
        expect(self.page.locator('#attachments')).to_contain_text('note.txt')

    def test_restored_running_activity_still_collapses_on_completion(self):
        turn = self.sessions['A']['history']['turns'][0]
        turn['status'] = 'inProgress'
        turn['items'].insert(0, dict(id='progress', type='agentMessage', phase='commentary', text='正在检查'))
        self.sessions['A']['active'] = True
        self.page.evaluate("app.sessions.selectSession('A')")
        self.assertTrue(self.page.evaluate("document.querySelector('[data-turn-id=t-A] .turn-activity').open"))
        self.page.evaluate("app.sessions.selectSession('B')")
        self.page.evaluate("app.sessions.selectSession('A')")
        self.page.evaluate("app.conversation.handleEvent({method:'turn/completed',params:{turn:{id:'t-A',status:'completed'}}})")
        self.assertFalse(self.page.evaluate("document.querySelector('[data-turn-id=t-A] .turn-activity').open"))

    def test_reasoning_plan_and_execution_stream(self):
        def event(event_method, **params):
            self.page.evaluate("e=>app.conversation.handleEvent(e)", dict(method=event_method, params=params))
        event('turn/started', turn=dict(id='flow', status='inProgress'))
        event('item/started', turnId='flow', item=dict(id='thought', type='reasoning', summary=[], content=[]))
        event('item/reasoning/summaryTextDelta', turnId='flow', itemId='thought', summaryIndex=0, delta='检查调用链')
        event('item/reasoning/summaryPartAdded', turnId='flow', itemId='thought', summaryIndex=1)
        event('item/reasoning/summaryTextDelta', turnId='flow', itemId='thought', summaryIndex=1, delta='核对异常路径')
        summary = self.page.locator('.reasoning-entry')
        expect(summary).to_be_visible()
        summary.locator('summary').click()
        expect(summary).to_contain_text('检查调用链')
        expect(summary).to_contain_text('核对异常路径')
        event('item/completed', turnId='flow', item=dict(id='thought', type='reasoning', summary=['已核对调用链'], content=[]))
        expect(summary).to_contain_text('思考摘要')
        expect(summary).not_to_contain_text('检查调用链')
        self.assertTrue(summary.evaluate('el=>el.open'))
        plan = [dict(step='读取实现', status='completed'), dict(step='验证结果', status='inProgress')]
        event('turn/plan/updated', turnId='flow', plan=plan)
        event('turn/plan/updated', turnId='flow', plan=plan)
        expect(self.page.locator('.turn-plan')).to_have_count(1)
        expect(self.page.locator('.turn-plan li[data-status="inProgress"]')).to_have_text('◉验证结果')
        command = dict(id='cmd', type='commandExecution', status='inProgress', command='cat app.js', commandActions=[dict(type='read', name='app.js')])
        event('item/started', turnId='flow', item=command)
        expect(self.page.locator('.turn-thinking:visible')).to_have_text('正在读取文件')
        event('web/request', id=1, method='item/commandExecution/requestApproval', params=dict(turnId='flow', command='cat app.js'))
        expect(self.page.locator('.turn-thinking:visible')).to_have_text('等待操作许可')
        event('web/requestResolved', id=1)
        event('item/completed', turnId='flow', item=dict(command, status='failed', exitCode=1, aggregatedOutput='fixture error'))
        failed = self.page.locator('.activity-entry[data-status="failed"]')
        expect(failed).to_contain_text('执行失败')
        self.assertTrue(failed.evaluate('el=>el.open'))
        event('item/started', turnId='flow', item=dict(id='final', type='agentMessage', phase='final_answer', text='检查完成'))
        expect(self.page.locator('.turn-thinking:visible')).to_have_count(0)
        event('turn/completed', turn=dict(id='flow', status='interrupted'))
        expect(self.page.locator('.turn-status').last).to_contain_text('已停止')

    def test_exploration_grouping_and_file_editor_bridge(self):
        self.page.evaluate("""()=>{
            app.conversation.handleEvent({method:'turn/started',params:{turn:{id:'grouping'}}});
            for(const id of ['read1','read2'])app.conversation.handleEvent({method:'item/completed',params:{turnId:'grouping',item:{id,type:'commandExecution',status:'completed',command:'cat file.py',commandActions:[{type:'read',name:'file.py'}],exitCode:0}}});
            app.conversation.handleEvent({method:'item/completed',params:{turnId:'grouping',item:{id:'explanation',type:'agentMessage',phase:'commentary',text:'继续检查配置'}}});
            app.conversation.handleEvent({method:'item/completed',params:{turnId:'grouping',item:{id:'search',type:'commandExecution',status:'completed',command:'rg config',commandActions:[{type:'search',query:'config'}],exitCode:0}}});
            window.openedFile=null;window.codexEditor.openFile=async path=>{window.openedFile=path;};
            app.conversation.handleEvent({method:'item/completed',params:{turnId:'grouping',item:{id:'edit',type:'fileChange',status:'completed',changes:[{path:'/workspace/file.py',diff:'@@ -1 +1 @@\\n-old\\n+new'}]}}});
        }""")
        groups = self.page.locator('.exploration-group')
        expect(groups).to_have_count(2)
        expect(groups.first.locator(':scope > summary')).to_have_text('已浏览代码 · 2 项')
        self.page.locator('.file-change-card > summary').click()
        self.page.locator('.changed-file .file-link').click()
        self.assertEqual(self.page.evaluate('window.openedFile'), 'file.py')

    def test_history_restores_plan_and_reasoning(self):
        turn = self.sessions['A']['history']['turns'][0]
        turn['webPlan'] = dict(plan=[dict(step='验证完成', status='completed')])
        turn['items'].insert(0, dict(id='summary', type='reasoning', summary=['思考摘要内容'], content=[]))
        self.page.evaluate("app.sessions.selectSession('A')")
        expect(self.page.locator('.turn-plan')).to_have_count(1)
        self.page.evaluate("document.querySelector('[data-turn-id=t-A] .turn-activity').open=true")
        self.page.locator('.reasoning-entry > summary').click()
        expect(self.page.locator('.reasoning-entry')).to_contain_text('思考摘要内容')
        self.page.evaluate("app.sessions.selectSession('B')")
        self.page.evaluate("app.sessions.selectSession('A')")
        expect(self.page.locator('.reasoning-entry .activity-body')).to_be_visible()
        expect(self.page.locator('.turn-plan')).to_contain_text('验证完成')

    def test_search_rename_and_sidebar_layout(self):
        self.page.locator('#sessionSearch').fill('项目 B')
        expect(self.page.locator('.session-select')).to_have_count(1)
        self.page.locator('.session-menu-trigger').click()
        self.page.locator('#renameSession').click()
        self.page.locator('#sessionName').fill('电源检查')
        self.page.locator('#saveSessionName').click()
        expect(self.page.locator('#renameDialog')).not_to_be_visible()
        self.page.locator('#sessionSearch').fill('电源')
        expect(self.page.locator('.session-select')).to_have_text('电源检查')
        self.page.set_viewport_size({'width': 390, 'height': 844})
        expect(self.page.locator('#sidebar')).not_to_be_visible()
        self.assertEqual(self.page.locator('#chat').evaluate('el=>el.clientWidth'), 390)
        self.page.locator('#activitySessions').click()
        expect(self.page.locator('#sidebar')).to_be_visible()
        self.page.locator('.session-select').click()
        expect(self.page.locator('#chatTitle')).to_have_text('电源检查')
        expect(self.page.locator('#sidebar')).not_to_be_visible()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)


if __name__ == '__main__':
    unittest.main()
