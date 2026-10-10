"""Optional real-model/browser smoke test against an already running server.
Requires playwright + Chromium; consumes a small number of Codex turns.
"""
import json
import os
from pathlib import Path
import yaml
from playwright.sync_api import sync_playwright, expect

BASE=Path(__file__).resolve().parents[1]
config=yaml.safe_load(Path(os.environ.get('CODEX_WEB_CONFIG',BASE/'config.yaml')).read_text())
url=os.environ.get('CODEX_WEB_TEST_URL','http://127.0.0.1:5200')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True, executable_path=os.environ.get('CHROMIUM_EXECUTABLE'))
    page=browser.new_page(viewport={'width':1280,'height':900})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(url)
    page.locator('#username').fill(config['username'])
    page.locator('#password').fill(config['password'])
    page.get_by_role('button',name='登录',exact=True).click()
    expect(page.locator('#app')).to_be_visible()
    expect(page.locator('#sidebar')).to_be_visible()
    page.locator('#workspaceButton').click()
    page.locator('#directories').get_by_role('button',name='codex-web',exact=True).click()
    page.locator('#chooseDirectory').click()
    page.locator('#model').select_option('gpt-5.6-sol')
    page.locator('#effort').select_option('medium')
    page.locator('#newSession').click()
    expect(page.locator('#currentSession')).to_contain_text('Session:')
    sid=page.evaluate("localStorage.getItem('codexWebSession')")
    print('BROWSER session created',sid,flush=True)
    page.locator('#message').fill('这是前端测试。不要修改文件。请调用 shell 运行 pwd，然后记住测试词 蓝色松树。最后回答当前目录和测试词。')
    page.locator('#send').click()
    expect(page.locator('#stop')).to_be_enabled(timeout=30000)
    expect(page.locator('#messages')).to_contain_text('蓝色松树',timeout=90000)
    expect(page.locator('#stop')).to_be_disabled(timeout=90000)
    expect(page.locator('#messages .assistant').last).to_contain_text('蓝色松树',timeout=10000)
    expect(page.locator('#messages')).to_contain_text(str(BASE))
    assert not page.locator('#error').inner_text(),page.locator('#error').inner_text()
    print('BROWSER real shell cwd and first answer passed',flush=True)
    page.reload()
    expect(page.locator('#messages .assistant').last).to_contain_text('蓝色松树',timeout=30000)
    previous_count=page.locator('#messages .assistant').count()
    page.locator('#message').fill('刚才的测试词是什么？不要使用工具，只回答测试词。')
    page.locator('#send').click()
    expect(page.locator('#messages .assistant')).to_have_count(previous_count+1,timeout=90000)
    expect(page.locator('#messages .assistant').last).to_contain_text('蓝色松树',timeout=90000)
    expect(page.locator('#stop')).to_be_disabled(timeout=30000)
    print('BROWSER refresh and follow-up passed',flush=True)
    page.locator('#message').fill('这是停止测试，请用 shell 执行 sleep 30，完成后回答 WAIT_DONE。不要修改文件。')
    page.locator('#send').click()
    expect(page.locator('#stop')).to_be_enabled(timeout=30000)
    page.wait_for_timeout(1500)
    page.locator('#stop').click()
    expect(page.locator('#stop')).to_be_disabled(timeout=30000)
    expect(page.locator('#status')).to_have_text('已停止',timeout=10000)
    page.locator('#sessionList').click()
    expect(page.locator(f'#sessions button[data-session-id="{sid}"]')).to_be_visible()
    page.screenshot(path='/tmp/codex-web-browser.png',full_page=True)
    assert not errors,errors
    print('BROWSER stop, session list, no JavaScript errors passed',flush=True)
    (Path('/tmp')/'codex-web-browser-session').write_text(sid)
    browser.close()
