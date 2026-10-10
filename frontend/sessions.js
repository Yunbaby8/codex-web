import {$, api, fail, guarded} from './ui.js';

export function createSessions({conversation, composer, workbench}) {
  // Only this controller replaces the current session, workspace and SSE connection.
  let config, current=null, workspace='', source=null, browsing='', nativeCursor=null;
  const listedSessions=new Map();
  let sessionView='active', sessionMenuTarget=null, sessionMenuButton=null, renameTarget=null;
  let busy=false, selecting=false, generation=0, selectionGeneration=0;
  function setBusy(value){busy=value;composer.updateControls();}
  function showLogin() { source?.close(); source=null; conversation.setContextUsage(null); $('app').hidden=true; $('loginPanel').hidden=false; }
  function setWorkspace(path) {workspace=path; $('workspaceButton').textContent=path+' ▾';window.dispatchEvent(new CustomEvent('codex:workspace',{detail:{workspace:path}}));}
  function renderSidebarInfo(){
    const label=config?.authUser?.label||'未识别账号',authUser=$('authUser');
    authUser.textContent=label;authUser.title='当前 Codex 使用的 auth.json 标签：'+label;
  }
  let quotaLoading=false;
  function closeAccountPanel(restoreFocus=false){$('accountPanel').hidden=true;$('accountButton').setAttribute('aria-expanded','false');if(restoreFocus)$('accountButton').focus();}
  async function refreshQuota(){
    if(quotaLoading)return;quotaLoading=true;$('refreshQuota').disabled=true;
    $('quotaAccount').textContent='当前账号：'+(config?.authUser?.label||'未识别账号');
    $('quotaMessage').textContent='';$('quotaUpdated').textContent='正在读取…';
    for(const name of ['fiveHour','weekly']){$(name+'Remaining').textContent='读取中…';$(name+'Progress').hidden=true;$(name+'Reset').textContent='';}
    try{
      const data=await api('/account/limits');config.authUser=data.authUser;renderSidebarInfo();
      $('quotaAccount').textContent='当前账号：'+(data.authUser?.label||'未识别账号');
      for(const name of ['fiveHour','weekly']){
        const window=data.windows?.[name],value=window?.remainingPercent;
        const available=typeof value==='number'&&Number.isFinite(value);
        $(name+'Remaining').textContent=available?'剩余 '+value+'%':'暂不可用';
        const progress=$(name+'Progress');progress.hidden=!available;if(available){progress.value=value;progress.setAttribute('aria-valuetext','剩余 '+value+'%');}
        $(name+'Reset').textContent=window?.resetsAt?'重置时间：'+new Date(window.resetsAt*1000).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'重置时间暂不可用';
      }
      $('quotaMessage').textContent=data.message||'';
      $('quotaUpdated').textContent=data.updatedAt?'更新于 '+new Date(data.updatedAt*1000).toLocaleTimeString('zh-CN',{hour12:false}):'未取得最新额度';
    }catch(error){for(const name of ['fiveHour','weekly'])$(name+'Remaining').textContent='暂不可用';$('quotaMessage').textContent=error.message;$('quotaUpdated').textContent='刷新失败';}
    finally{quotaLoading=false;$('refreshQuota').disabled=false;}
  }
  $('accountButton').onclick=()=>{const open=$('accountPanel').hidden;$('accountPanel').hidden=!open;$('accountButton').setAttribute('aria-expanded',String(open));if(open){closeWorkspaceDropdown();refreshQuota();}};
  $('refreshQuota').onclick=refreshQuota;
  document.addEventListener('click',event=>{if(!event.target.closest('#accountControls'))closeAccountPanel();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('accountPanel').hidden){event.preventDefault();closeAccountPanel(true);}});
  async function boot() {
    config=await api('/config');
    renderSidebarInfo();
    $('permissionLabel').textContent=({'read-only':'只读','workspace-write':'工作区写入','danger-full-access':'完全访问'})[config.sandbox]||config.sandbox;
    $('permission').title='后端权限：'+config.sandbox;
    $('model').replaceChildren(...config.models.map(m=>new Option(m.id.replace(/^gpt-/, 'GPT-').replace(/-(astra|sol|terra|luna)$/, (_,name)=>' '+name[0].toUpperCase()+name.slice(1)),m.id)));
    composer.fillEfforts('medium');
    composer.updateFastControl();
    $('roots').replaceChildren(...config.roots.map(r=>new Option(r,r)));
    setWorkspace(config.roots[0]);
    $('loginPanel').hidden=true; $('app').hidden=false;
    const saved=localStorage.getItem('codexWebSession');
    if(saved) {try {await selectSession(saved);} catch(e){localStorage.removeItem('codexWebSession');fail(e);}}
    if(!current)composer.restoreDraft();
    await openSessions();
  }
  $('loginForm').onsubmit=async e=>{e.preventDefault();try{await api('/login','POST',{username:$('username').value,password:$('password').value});$('password').value='';$('loginError').textContent='';await boot();}catch(e){$('loginError').textContent=e.message;}};
  $('reloadCodex').onclick=guarded(async()=>{
    if(busy)throw Error('当前会话正在执行，请等待完成或 Stop 后再重新加载 Codex');
    $('reloadCodex').disabled=true;$('reloadCodex').textContent='重载中…';$('status').textContent='正在重新加载 Codex…';
    try{await api('/codex/reload','POST',{});location.reload();}
    finally{$('reloadCodex').disabled=false;$('reloadCodex').textContent='重载';}
  });
  $('logout').onclick=guarded(async()=>{await api('/logout','POST',{});showLogin();});
  let directoryRequest=0;
  async function browse(path) {
    const ticket=++directoryRequest;
    $('chooseDirectory').disabled=true;
    const data=await api('/workspaces?path='+encodeURIComponent(path));
    if(ticket!==directoryRequest)return;
    browsing=data.path;$('chooseDirectory').disabled=false;
    $('directoryPath').textContent=data.path;
    $('directories').replaceChildren();
    const dirs=[...(data.parent?[{name:'↑ 上一级',path:data.parent}]:[]),...data.directories];
    for(const d of dirs){const b=document.createElement('button');b.textContent=d.name;b.onclick=guarded(()=>browse(d.path));$('directories').append(b);}
  }
  function closeWorkspaceDropdown(restoreFocus=false){
    $('directoryDropdown').hidden=true;$('workspaceButton').setAttribute('aria-expanded','false');
    directoryRequest++;
    if(restoreFocus)$('workspaceButton').focus();
  }
  $('workspaceButton').onclick=guarded(async()=>{
    if(!$('directoryDropdown').hidden){closeWorkspaceDropdown();return;}
    $('directoryDropdown').hidden=false;$('workspaceButton').setAttribute('aria-expanded','true');
    $('directories').textContent='正在读取目录…';$('directoryPath').textContent=workspace;
    $('roots').value=[...config.roots].sort((a,b)=>b.length-a.length).find(root=>workspace===root||workspace.startsWith(root.replace(/\/$/,'')+'/'))||config.roots[0];
    $('roots').focus();await browse(workspace);
  });
  document.addEventListener('click',event=>{
    if(!$('directoryDropdown').hidden&&!event.target.closest('#windowTitle .workspace'))closeWorkspaceDropdown();
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&!$('directoryDropdown').hidden){event.preventDefault();closeWorkspaceDropdown(true);}
  });
  document.addEventListener('focusin',event=>{
    if(!$('directoryDropdown').hidden&&!event.target.closest('#windowTitle .workspace'))closeWorkspaceDropdown();
  });
  $('roots').onchange=guarded(()=>browse($('roots').value));
  $('closeDirectory').onclick=()=>closeWorkspaceDropdown(true);
  $('chooseDirectory').onclick=()=>{setWorkspace(browsing);closeWorkspaceDropdown(true);if(current&&current.cwd!==workspace)$('status').textContent='目录已选，请 New Session 应用';};
  async function newSession(fromSubmit=false){if(composer.submitting&&!fromSubmit)throw Error('消息正在发送，请稍后新建会话');const s=await api('/sessions','POST',{cwd:workspace,model:$('model').value,effort:$('effort').value});await selectSession(s.id,fromSubmit);}
  $('newSession').onclick=guarded(()=>newSession());
  async function selectSession(id,fromSubmit=false){
    if(composer.submitting&&!fromSubmit)throw Error('消息正在发送，请稍后切换会话');
    const ticket=++selectionGeneration;
    selecting=true;$('send').disabled=true;$('status').textContent='正在打开会话…';
    let s;
    try{s=await api('/sessions/'+encodeURIComponent(id));}
    finally{if(ticket===selectionGeneration){selecting=false;$('send').disabled=composer.submitting;}}
    if(ticket!==selectionGeneration)return;
    conversation.saveReadingView();composer.saveDraft();
    const previousId=current?.id||'new';
    source?.close();source=null;const streamGeneration=++generation;
    current=s;conversation.setContextUsage(s.tokenUsage);renderSidebarInfo();const belongsToView=sessionView==='archived'?Boolean(s.archived):!s.archived;if(belongsToView)listedSessions.set(id,{...s,web:true});else listedSessions.delete(id);renderSessions();localStorage.setItem('codexWebSession',id);setWorkspace(s.cwd);
    $('chatTitle').textContent=s.title||s.name||'新对话';
    if(config.models.some(m=>m.id===s.model)){$('model').value=s.model;composer.fillEfforts(s.effort);}
    $('currentSession').textContent='Session: '+id;
    conversation.restoreSession(s);
    if(fromSubmit)composer.adoptDraft(previousId);
    else composer.restoreDraft();
    window.dispatchEvent(new CustomEvent('codex:files-changed',{detail:{workspace:s.cwd}}));
    workbench.collapseOnNarrowScreen();
    source=new EventSource('/api/sessions/'+encodeURIComponent(id)+'/events?after='+encodeURIComponent(s.cursor||0));
    source.onmessage=e=>{if(streamGeneration===generation)conversation.handleEvent(JSON.parse(e.data));};
  source.onopen=()=>{$('status').textContent=busy?'Codex 正在工作，可继续引导…':'已连接';};
    source.onerror=()=>{$('status').textContent='连接中断，正在重连…';};
    source.addEventListener('expired',()=>{showLogin();$('loginError').textContent='登录已过期，请重新登录';});
  }
  function closeSessionMenu(){
    $('sessionMenu').hidden=true;
    sessionMenuButton?.setAttribute('aria-expanded','false');
    sessionMenuTarget=null;sessionMenuButton=null;
  }
  function openSessionMenu(event,session){
    event.stopPropagation();
    const button=event.currentTarget,menu=$('sessionMenu');
    if(sessionMenuButton===button&&!menu.hidden){closeSessionMenu();return;}
    closeSessionMenu();sessionMenuTarget=session;sessionMenuButton=button;button.setAttribute('aria-expanded','true');
    const active=session.id===current?.id?busy:Boolean(session.active);
    $('archiveSession').textContent=sessionView==='archived'?'已归档':'归档会话';$('archiveSession').disabled=active||sessionView==='archived';$('deleteSession').disabled=active;menu.hidden=false;
    const rect=button.getBoundingClientRect(),margin=8;
    let left=rect.right-menu.offsetWidth,top=rect.bottom+4;
    if(left<margin)left=margin;
    if(top+menu.offsetHeight>window.innerHeight-margin)top=rect.top-menu.offsetHeight-4;
    menu.style.left=left+'px';menu.style.top=Math.max(margin,top)+'px';
  }
  function clearCurrentSession(message){
    conversation.saveReadingView();composer.saveDraft();
    generation++;source?.close();source=null;current=null;localStorage.removeItem('codexWebSession');
    conversation.reset();renderSidebarInfo();setBusy(false);
    const empty=document.createElement('div');empty.id='empty';empty.textContent='选择项目目录，创建 Session，开始与 Codex 对话。';$('messages').append(empty);
    $('currentSession').textContent='尚未创建 Session';$('status').textContent=message;
    $('chatTitle').textContent='新对话';composer.restoreDraft();conversation.updateReadingPosition();
  }
  async function applySessionAction(action,session){
    if(!session)return;
    closeSessionMenu();
    if(action==='archive')await api('/sessions/'+encodeURIComponent(session.id)+'/archive','POST',{});
    else await api('/sessions/'+encodeURIComponent(session.id),'DELETE');
    listedSessions.delete(session.id);
    if(current?.id===session.id)clearCurrentSession(action==='archive'?'会话已归档':'会话已删除');
    else $('status').textContent=action==='archive'?'会话已归档':'会话已删除';
    renderSessions();
    if(action==='delete'){
      composer.forgetSession(session.id);conversation.forgetSession(session.id);
    }
  }
  $('renameSession').onclick=()=>{
    renameTarget=sessionMenuTarget;if(!renameTarget)return;closeSessionMenu();
    $('sessionName').value=renameTarget.title||renameTarget.name||renameTarget.preview||'';$('renameError').textContent='';
    $('renameDialog').showModal();$('sessionName').select();
  };
  $('cancelRename').onclick=()=>$('renameDialog').close();
  $('renameForm').onsubmit=async event=>{
    event.preventDefault();const target=renameTarget,title=$('sessionName').value.trim();
    if(!title){$('renameError').textContent='请输入会话标题';return;}
    $('saveSessionName').disabled=true;
    try{
      if(!target.web)await api('/sessions/'+encodeURIComponent(target.id)+'/import','POST',{model:$('model').value,effort:$('effort').value});
      const result=await api('/sessions/'+encodeURIComponent(target.id),'PATCH',{title});
      Object.assign(target,result,{web:true});listedSessions.set(target.id,target);
      if(current?.id===target.id){current.title=result.title;$('chatTitle').textContent=result.title;}
      renderSessions();$('renameDialog').close();
    }catch(error){$('renameError').textContent=error.message;}
    finally{$('saveSessionName').disabled=false;}
  };
  $('archiveSession').onclick=guarded(async e=>{e.stopPropagation();const session=sessionMenuTarget;await applySessionAction('archive',session);});
  $('deleteSession').onclick=guarded(async e=>{
    e.stopPropagation();
    const session=sessionMenuTarget;if(!session)return;
    $('status').textContent='正在删除会话…';
    await applySessionAction('delete',session);
  });
  document.addEventListener('click',e=>{if(!$('sessionMenu').contains(e.target))closeSessionMenu();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeSessionMenu();});
  $('sessions').onscroll=closeSessionMenu;
  function setSessionView(view){
    sessionView=view==='archived'?'archived':'active';
    const active=sessionView==='active';
    $('sessionsTab').classList.toggle('selected',active);$('sessionsTab').setAttribute('aria-selected',String(active));
    $('archiveTab').classList.toggle('selected',!active);$('archiveTab').setAttribute('aria-selected',String(!active));
  }
  function sessionTimestamp(session){const value=Number(session.created||session.createdAt||session.created_at||session.updated||session.updatedAt)||0;return value>1e12?value/1000:value;}
  function isToday(session){const timestamp=sessionTimestamp(session);if(!timestamp)return false;const date=new Date(timestamp*1000),today=new Date();return date.getFullYear()===today.getFullYear()&&date.getMonth()===today.getMonth()&&date.getDate()===today.getDate();}
  function sessionAttachmentTitle(session){
    return (session.attachments||[]).map(attachment=>'用户上传的附件："'+attachment.name+'"\nLinux 文件路径："'+attachment.path+'"').join('\n');
  }
  function renderSessions(){
    closeSessionMenu();
    $('sessions').replaceChildren();
    const query=$('sessionSearch').value.trim().toLocaleLowerCase(),entries=[...listedSessions.values()].filter(s=>String(s.title||s.name||s.preview||s.id).toLocaleLowerCase().includes(query)).sort((a,b)=>(b.updated||b.updatedAt||0)-(a.updated||a.updatedAt||0));
    $('sessionCount').textContent=listedSessions.size+(nativeCursor?' +':'');
    $('searchScope').textContent=query?'已加载 '+listedSessions.size+' 个会话，匹配 '+entries.length+' 个':'仅搜索当前列表已加载的会话';
    if(!entries.length){$('sessions').textContent=query?'已加载的会话中没有匹配结果。':sessionView==='archived'?'暂未找到已归档会话。':'允许目录内暂未找到会话。';return;}
    for(const s of entries){
      const row=document.createElement('div'),b=document.createElement('button'),more=document.createElement('button');row.className='session-row';b.className='session-select';
      const title=String(s.title||s.name||s.preview||s.id),titleNode=document.createElement('span');titleNode.className='session-title';titleNode.textContent=(s.active?'● ':'')+title.replace(/\s+/g,' ');b.append(titleNode);
      const today=isToday(s),attachments=(today?s.attachments:[])||[];
      if(today){const meta=document.createElement('span');meta.className='session-meta';meta.textContent='今日新开'+(attachments.length?' · 📎 '+attachments.map(attachment=>attachment.name).join('、'):'');meta.title=attachments.length?sessionAttachmentTitle(s):'这个会话创建于今天';b.append(meta);}
      b.title=title+'\n'+s.cwd+'\n'+s.id+(today?'\n今日新开':'')+(attachments.length?'\n'+sessionAttachmentTitle(s):'');
      b.dataset.sessionId=s.id;
      if(current?.id===s.id){b.classList.add('selected');b.setAttribute('aria-current','true');}
      b.onclick=guarded(async()=>{
        b.disabled=true;
        try{
          if(!s.web)await api('/sessions/'+s.id+'/import','POST',{model:$('model').value,effort:$('effort').value});
          await selectSession(s.id);
        }finally{b.disabled=false;}
      });
      more.type='button';more.className='session-menu-trigger';more.textContent='⋯';more.title='会话操作';more.setAttribute('aria-label','打开“'+title+'”的会话操作');more.setAttribute('aria-haspopup','menu');more.setAttribute('aria-expanded','false');more.onclick=e=>openSessionMenu(e,s);
      row.append(b,more);$('sessions').append(row);
    }
  }
  async function loadNativeSessions(){
    $('nativeSessions').disabled=true;
    try{
      let result;
      do{
        result=await api('/native-sessions'+(nativeCursor?'?cursor='+encodeURIComponent(nativeCursor):''));
        nativeCursor=result.nextCursor;
      }while(!result.data.length&&nativeCursor);
      for(const s of result.data)if(!listedSessions.has(s.id))listedSessions.set(s.id,s);
      renderSessions();
      $('nativeSessions').hidden=!nativeCursor;
      $('nativeSessions').textContent='加载更多会话';
    }finally{$('nativeSessions').disabled=false;}
  }
  async function openSessions(view=sessionView){
    setSessionView(view);
    $('sessions').textContent=sessionView==='archived'?'正在读取已归档会话…':'正在读取 Linux 上的 Codex 会话…';
    $('nativeSessions').hidden=true;nativeCursor=null;listedSessions.clear();
    const sessions=await api('/sessions'+(sessionView==='archived'?'?archived=true':''));
    for(const s of sessions)listedSessions.set(s.id,{...s,web:true});
    renderSessions();
    if(sessionView==='active')await loadNativeSessions();
  }
  $('sessionList').onclick=guarded(openSessions);
  $('sessionSearch').addEventListener('input',renderSessions);
  $('nativeSessions').onclick=guarded(loadNativeSessions);
  $('sessionsTab').onclick=guarded(()=>openSessions('active'));
  $('archiveTab').onclick=guarded(()=>openSessions('archived'));

  async function updateAfterSend(sid,result,hasAttachments){
    const listed=listedSessions.get(sid);
    if(result.title){if(current?.id===sid){current.title=result.title;$('chatTitle').textContent=result.title;}if(listed)listed.title=result.title;}
    if(listed){
      listed.updated=Date.now()/1000;
      if(hasAttachments){listed.attachments=await api(`/sessions/${encodeURIComponent(sid)}/attachments`);if(current?.id===sid)current.attachments=listed.attachments;renderSidebarInfo();}
      renderSessions();
    }
  }
  window.addEventListener('codex:login-required',showLogin);
  return {get current(){return current;},get workspace(){return workspace;},get config(){return config;},
    get busy(){return busy;},get selecting(){return selecting;},
    boot,selectSession,newSession,setWorkspace,setBusy,clearCurrentSession,closeSessionMenu,updateAfterSend};
}
