// Public bridge: openFile(relativePath, {line}), refreshFile(relativePath).
// Workspace changes arrive from the existing chat application's setWorkspace.
export function createEditor(workbench) {
  const el = id => document.getElementById(id);
  const files = new Map(), opening = new Map();
  let workspace = '', active = null, editor = null, monaco = null, loading = null, selection = 0, treeGeneration = 0;
  let syncGeneration=0, syncTimer=null;
  let gitGeneration=0, diffEditor=null, diffModels=[], diffVisible=false;
  const keyFor = (root, path) => JSON.stringify([root,path]);
  const text = file => file.model.getValue(undefined, true);
  const dirty = file => text(file) !== file.savedText;
  const notice = message => { el('editorNotice').textContent = message; };
  const run = fn => async (...args) => { try { await fn(...args); } catch(error) { notice(error.message); } };

  async function request(kind, root, path, body) {
    const response = await fetch('/api/editor/'+kind+(body?'':'?'+new URLSearchParams({workspace:root,path})), {
      method:body?'PUT':'GET',headers:{'Content-Type':'application/json','X-Codex-Web':'1'},
      body:body?JSON.stringify({workspace:root,path,...body}):undefined
    });
    const data = await response.json();
    if(!response.ok){const error=Error(data.detail || '文件请求失败');error.status=response.status;throw error;}
    return data;
  }
  let quickPaths=[],quickMatches=[],quickSelected=0,quickRoot='',quickController=null,quickIndexNotice='';
  function quickQuery(){
    const value=el('quickOpenInput').value.trim(),match=value.match(/^(.*?):(\d+)(?::(\d+))?$/);
    return {query:(match?match[1]:value).toLocaleLowerCase(),line:match?Number(match[2]):null,column:match?Number(match[3]||1):1};
  }
  function renderQuickMatches(){
    const {query}=quickQuery();
    const ranked=[];
    for(const path of quickPaths){
      const lower=path.toLocaleLowerCase(),name=lower.split('/').pop();let score=0;
      if(query){
        if(name===query)score=0;
        else if(name.startsWith(query))score=1;
        else if(lower.includes(query))score=2;
        else{let cursor=0;for(const char of lower)if(char===query[cursor])cursor++;if(cursor!==query.length)continue;score=3;}
      }else score=files.has(keyFor(quickRoot,path))?0:1;
      ranked.push({path,score});
    }
    ranked.sort((a,b)=>a.score-b.score||a.path.length-b.path.length||a.path.localeCompare(b.path));
    quickMatches=ranked.slice(0,100).map(item=>item.path);quickSelected=0;
    const host=el('quickOpenResults');host.replaceChildren();
    for(const [index,path] of quickMatches.entries()){
      const row=document.createElement('div'),name=document.createElement('strong'),directory=document.createElement('span');
      row.className='quick-open-result';row.id='quick-file-'+index;row.setAttribute('role','option');row.title=path;
      name.textContent=path.split('/').pop();directory.textContent=path.includes('/')?path.slice(0,path.lastIndexOf('/')):'工作区根目录';row.append(name,directory);
      row.onmousedown=event=>event.preventDefault();row.onclick=()=>{quickSelected=index;openQuickSelection();};host.append(row);
    }
    selectQuickResult();host.scrollTop=0;
    el('quickOpenHint').textContent=(ranked.length?'找到 '+ranked.length+' 个文件'+(ranked.length>100?'，显示前 100 个':''):'没有匹配的文件')+(quickIndexNotice?' · '+quickIndexNotice:'');
  }
  function selectQuickResult(){
    [...el('quickOpenResults').children].forEach((row,index)=>row.setAttribute('aria-selected',String(index===quickSelected)));
    const row=el('quickOpenResults').children[quickSelected];
    if(row){el('quickOpenInput').setAttribute('aria-activedescendant',row.id);row.scrollIntoView({block:'nearest'});}
    else el('quickOpenInput').removeAttribute('aria-activedescendant');
  }
  async function openQuickSelection(){
    const path=quickMatches[quickSelected];if(!path||quickRoot!==workspace)return;
    const root=quickRoot,options=quickQuery();
    try{await openFile(path,options);if(root===workspace){el('quickOpenDialog').close();editor?.focus();}}
    catch(error){if(root===workspace)el('quickOpenHint').textContent=error.message;}
  }
  async function showQuickOpen(){
    const dialog=el('quickOpenDialog');if(dialog.open){el('quickOpenInput').focus();el('quickOpenInput').select();return;}
    if(!workspace){notice('请先选择工作区');return;}
    quickRoot=workspace;quickPaths=[];quickMatches=[];quickIndexNotice='';
    el('quickOpenInput').value='';el('quickOpenInput').removeAttribute('aria-activedescendant');el('quickOpenResults').replaceChildren();el('quickOpenHint').textContent='正在查找工作区文件…';
    dialog.showModal();el('quickOpenInput').focus();
    quickController?.abort();const controller=new AbortController();quickController=controller;
    try{
      const response=await fetch('/api/editor/index?'+new URLSearchParams({workspace:quickRoot}),{signal:controller.signal}),data=await response.json();
      if(!response.ok)throw Error(data.detail||'文件列表读取失败');
      if(controller!==quickController||!dialog.open||quickRoot!==workspace)return;
      quickPaths=data.paths;quickIndexNotice=[data.truncated?'项目较大，当前列表不完整，可选择更小的工作区':'',data.skipped?'部分目录无法访问':''].filter(Boolean).join('；');renderQuickMatches();
    }catch(error){if(!controller.signal.aborted)el('quickOpenHint').textContent=error.message;}
  }
  el('quickOpenButton').onclick=showQuickOpen;
  el('quickOpenInput').oninput=renderQuickMatches;
  el('quickOpenInput').onkeydown=event=>{
    if(event.isComposing)return;
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();quickSelected=Math.max(0,Math.min(quickMatches.length-1,quickSelected+(event.key==='ArrowDown'?1:-1)));selectQuickResult();}
    else if(event.key==='Enter'){event.preventDefault();openQuickSelection();}
  };
  el('quickOpenDialog').addEventListener('close',()=>{if(el('quickOpenDialog').open)return;quickController?.abort();quickController=null;});
  el('quickOpenDialog').addEventListener('click',event=>{if(event.target===el('quickOpenDialog')){const rect=event.target.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)event.target.close();}});
  document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&!event.shiftKey&&!event.altKey&&event.key.toLowerCase()==='p'&&!el('app').hidden){event.preventDefault();event.stopPropagation();showQuickOpen();}},true);
  function editorTheme(){return ({'classic-black':'codex-black',tech:'codex-blue',white:'codex-white',green:'codex-green',red:'codex-red',yellow:'codex-yellow',beige:'codex-beige'})[document.documentElement.dataset.theme]||'codex-black';}
  async function loadEditor() {
    if(!loading)loading=(async()=>{
      const css=document.createElement('link');css.rel='stylesheet';css.href='/static/vendor/editor-entry.css';document.head.append(css);
      const module=await import('/static/vendor/editor-entry.js');monaco=module.monaco;
      for(const [name,base,background,foreground,panel,border,highlight,selection,lineNumber] of [
        ['black','vs-dark','#000000','#e6e6e6','#0c0c0c','#292929','#181818','#242424','#929292'],
        ['white','vs','#ffffff','#252525','#f5f5f5','#e2e2e2','#f5f5f5','#ebebeb','#737373'],
        ['blue','vs-dark','#11151b','#e1e5eb','#191e26','#303945','#222832','#2c3440','#909eae'],
        ['green','vs-dark','#121714','#e1e7e2','#1b211d','#344239','#242c26','#303b33','#93a598'],
        ['red','vs-dark','#181516','#e9e2e4','#221e20','#453a3e','#2d2729','#3c3336','#a99a9f'],
        ['yellow','vs','#fffcf4','#373228','#f5f0e3','#ded5c2','#f5f0e3','#ebe3d1','#7b705c'],
        ['beige','vs','#faf8f3','#37342e','#f0ede6','#ddd7cd','#f0ede6','#e6e1d8','#787165']
      ])monaco.editor.defineTheme('codex-'+name,{base,inherit:true,rules:module.highlightingRules(base==='vs'),colors:{
        'editor.background':background,'editorGutter.background':background,'editor.foreground':foreground,
        'editor.lineHighlightBackground':highlight,'editor.lineHighlightBorder':highlight,
        'editorLineNumber.foreground':lineNumber,'editor.selectionBackground':selection,
        'editorWidget.background':panel,'editorWidget.border':border,'editorOverviewRuler.background':background
      }});
      editor=monaco.editor.create(el('monacoHost'),{model:null,theme:editorTheme(),automaticLayout:true,
        fontSize:15,lineNumbers:'on',folding:true,autoIndent:'full',minimap:{enabled:false},
        scrollBeyondLastLine:false,smoothScrolling:false,cursorBlinking:'solid',cursorSmoothCaretAnimation:'off',
        wordBasedSuggestions:'off',quickSuggestions:false,
        suggestOnTriggerCharacters:false,tabSize:4,insertSpaces:true});
      editor.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyS,run(save));
      editor.onDidChangeCursorPosition(updateEditorStatus);
      editor.onDidChangeCursorSelection(updateEditorStatus);
      editor.addAction({id:'codex.askSelection',label:'询问 Codex',precondition:'editorHasSelection',contextMenuGroupId:'navigation',contextMenuOrder:1.5,run:askSelection});
    })().catch(error=>{loading=null;throw Error('Monaco 加载失败，请检查本地静态资源：'+error.message);});
    return loading;
  }
  function language(path) {
    const name=path.split('/').pop(),ext=name.split('.').pop().toLowerCase();
    if(/^(?:GNUmakefile|makefile)$/i.test(name)||ext==='mk')return 'makefile';
    if(['bb','bbappend','inc','bbclass'].includes(ext))return 'bitbake';
    if(['service','socket','target','timer','mount','automount','path','slice','scope'].includes(ext))return 'systemd';
    const known={c:'c',h:'cpp',cc:'cpp',cpp:'cpp',cxx:'cpp',hpp:'cpp',hh:'cpp',hxx:'cpp',py:'python',sh:'shell',bash:'shell',zsh:'shell',yaml:'yaml',yml:'yaml',json:'json',md:'markdown',markdown:'markdown'};
    return known[ext] || monaco.languages.getLanguages().find(item=>item.extensions?.includes('.'+ext)||item.filenames?.includes(name))?.id || 'plaintext';
  }
  function askSelection(){
    if(diffVisible||!active||!editor)return;
    const selections=(editor.getSelections()||[]).filter(range=>!range.isEmpty()).map(range=>({
      startLine:range.startLineNumber,endLine:range.endLineNumber-(range.endColumn===1&&range.endLineNumber>range.startLineNumber?1:0),
      startColumn:range.startColumn,endColumn:range.endColumn,content:active.model.getValueInRange(range)
    }));
    if(!selections.length)return;
    window.dispatchEvent(new CustomEvent('codex:ask-selection',{detail:{workspace:active.workspace,path:active.path,language:active.model.getLanguageId(),unsaved:dirty(active),selections}}));
  }
  el('askCodex').onclick=askSelection;
  function updateEditorStatus() {
    el('askCodex').disabled=diffVisible||!active||!editor?.getSelections()?.some(range=>!range.isEmpty());
    if(diffVisible){el('editorPosition').textContent='';el('editorEncoding').textContent='UTF-8 · 只读';el('editorLanguage').textContent=diffModels[1]?.getLanguageId()||'';return;}
    const position=active&&editor?.getPosition();
    el('editorPosition').textContent=position?'行 '+position.lineNumber+'，列 '+position.column:'';
    el('editorEncoding').textContent=active?'UTF-8 · '+(active.model.getEOL()==='\r\n'?'CRLF':'LF'):'';
    el('editorLanguage').textContent=active?.model.getLanguageId()||'';
  }
  function render(rebuildTabs=true) {
    if(rebuildTabs)el('editorTabs').replaceChildren();
    for(const [key,file] of files) {
      if(file.workspace!==workspace)continue;
      if(!rebuildTabs){
        const label=(dirty(file)?'● ':'')+file.path.split('/').pop();
        if(file.tabButton&&file.tabButton.textContent!==label){file.tabButton.textContent=label;file.tabButton.setAttribute('aria-label',label);}
        continue;
      }
      const tab=document.createElement('div'),button=document.createElement('button'),close=document.createElement('button');
      file.tabButton=button;
      tab.className='editor-tab';tab.classList.toggle('active',!diffVisible&&file===active);
      button.type='button';button.setAttribute('role','tab');button.setAttribute('aria-selected',String(!diffVisible&&file===active));
      button.textContent=(dirty(file)?'● ':'')+file.path.split('/').pop();button.title=file.path;button.setAttribute('aria-label',button.textContent);button.onclick=()=>activate(file);
      close.type='button';close.className='tab-close';close.title='关闭 '+file.path;close.setAttribute('aria-label',close.title);close.onclick=()=>closeFile(key);
      tab.append(button,close);el('editorTabs').append(tab);
    }
    updateEditorStatus();
    el('editorPath').textContent=diffVisible?diffModels[1].uri.path.slice(1):active?.path||'选择左侧文件开始编辑';
    el('editorPath').title=el('editorPath').textContent;
    el('saveFile').disabled=diffVisible||!active||active.saving||!dirty(active);
    el('reloadFile').disabled=diffVisible||!active||active.saving;
    el('editorEmpty').hidden=diffVisible||Boolean(active);el('monacoHost').hidden=diffVisible||!active;
  }
  function activate(file) {
    closeDiff();
    selection++;
    if(active&&editor)active.view=editor.saveViewState();
    active=file;editor?.setModel(file?.model||null);
    if(file?.view)editor.restoreViewState(file.view);
    render();notice(file?.externalChange||'');editor?.layout();if(file)editor.focus();
  }
  function closeFile(key) {
    const file=files.get(key);if(!file||file.saving)return;
    if(dirty(file)&&!confirm('放弃 '+file.path+' 的未保存修改并关闭？'))return;
    const visible=[...files.values()].filter(f=>f.workspace===workspace),index=visible.indexOf(file);
    if(active===file)activate(visible[index+1]||visible[index-1]||null);
    file.listener.dispose();file.model.dispose();files.delete(key);
    if(!active)activate([...files.values()].find(f=>f.workspace===workspace)||null);else render();
  }
  async function openFile(path, {line=null,column=1}={}) {
    if(!workspace)throw Error('请先选择工作区');
    const root=workspace,key=keyFor(root,path),ticket=++selection;
    notice('正在打开 '+path+'…');
    if(!files.has(key)) {
      if(!opening.has(key))opening.set(key,(async()=>{
        const [data]=await Promise.all([request('file',root,path),loadEditor()]);
        const uri=monaco.Uri.from({scheme:'workspace',authority:encodeURIComponent(root),path:'/'+path});
        const model=monaco.editor.createModel(data.content,language(path),uri);
        model.updateOptions({tabSize:4,insertSpaces:language(path)!=='makefile'});
        const file={workspace:root,path,model,revision:data.revision,savedText:model.getValue(undefined,true),saving:false};
        file.listener=model.onDidChangeContent(()=>{if(file.workspace===workspace)render(false);});
        files.set(key,file);return file;
      })().finally(()=>opening.delete(key)));
      await opening.get(key);
    }
    if(workspace!==root||ticket!==selection)return;
    activate(files.get(key));
    workbench.showEditor();
    if(line!==null){const position={lineNumber:Math.max(1,Math.min(active.model.getLineCount(),Number(line)||1)),column:Math.max(1,Number(column)||1)};editor.setPosition(position);editor.revealPositionInCenterIfOutsideViewport(position);}
  }
  async function save() {
    const file=active;if(diffVisible||!file||file.saving||!dirty(file))return;
    const content=text(file);file.saving=true;render();notice('正在保存…');
    try {
      const data=await request('file',file.workspace,file.path,{content,revision:file.revision});
      file.revision=data.revision;file.savedText=content;file.externalChange='';
      notice('已保存 '+file.path+(dirty(file)?'；还有保存期间的新修改':''));
      window.dispatchEvent(new CustomEvent('codex:file-saved',{detail:{workspace:file.workspace,path:file.path}}));
      if(file.workspace===workspace)refreshGit();
    } finally {file.saving=false;render();}
  }
  async function refreshFile(path, {discard=false,root=workspace,silent=false}={}) {
    const file=files.get(keyFor(root,path));if(!file||file.saving)return;
    const version=file.model.getVersionId(),revision=file.revision;
    const report=message=>{file.externalChange=message;if(!silent&&root===workspace)notice(message);return message;};
    let data;
    try{data=await request('file',root,path);}
    catch(error){
      if(file.model.isDisposed())return;
      return report(path+'：'+(error.status===404?'磁盘文件已删除，已保留打开的内容。':error.message));
    }
    if(file.model.isDisposed()||root!==workspace||file.saving||file.revision!==revision)return;
    if(data.revision===revision&&!discard){file.externalChange='';return;}
    if(file.model.getVersionId()!==version)return report(path+'：加载期间出现新编辑，已保留。');
    if(dirty(file)&&!discard)return report(path+'：磁盘文件已变化，已保留未保存内容；可用“重新加载”读取磁盘，保存时仍会检查冲突。');
    const view=active===file?editor.saveViewState():file.view;
    if(data.content!==text(file))file.model.setValue(data.content);
    file.savedText=text(file);file.revision=data.revision;file.externalChange='';
    if(active===file&&view)editor.restoreViewState(view);
    render();if(!silent)notice('已从磁盘加载 '+path);
  }
  async function syncWorkspace(root=workspace) {
    if(!root||root!==workspace)return;
    const ticket=++syncGeneration;
    await Promise.all([refreshTree(),refreshGit()]);
    const warnings=[];
    for(const file of [...files.values()]){
      if(ticket!==syncGeneration||root!==workspace)return;
      if(file.workspace!==root)continue;
      const warning=await refreshFile(file.path,{root,silent:true});if(warning)warnings.push(warning);
    }
    if(ticket===syncGeneration&&root===workspace)notice(warnings.join('\n')||active?.externalChange||'');
  }
  async function loadTree(host,path,root,token,expanded=new Set()) {
    host.textContent='正在读取…';
    try {
      const data=await request('tree',root,path);
      if(token!==treeGeneration||root!==workspace)return;
      host.replaceChildren();
      for(const entry of data.entries) {
        if(entry.directory) {
          const details=document.createElement('details'),summary=document.createElement('summary'),children=document.createElement('div');
          summary.textContent=entry.name;summary.title=entry.path;children.className='tree-children';details.dataset.path=entry.path;details.append(summary,children);
          let loaded=false;details.ontoggle=()=>{if(details.open&&!loaded){loaded=true;loadTree(children,entry.path,root,token);}};host.append(details);
          if(expanded.has(entry.path)){loaded=true;details.open=true;await loadTree(children,entry.path,root,token,expanded);if(token!==treeGeneration||root!==workspace)return;}
        } else {
          const button=document.createElement('button');button.type='button';button.textContent=entry.name;button.title=entry.path;button.onclick=run(()=>openFile(entry.path));host.append(button);
        }
      }
      if(!data.entries.length)host.textContent='（空目录或已忽略）';
    } catch(error) {
      if(token!==treeGeneration)return;
      host.textContent=error.message;const retry=document.createElement('button');retry.textContent='重试';retry.onclick=()=>loadTree(host,path,root,token);host.append(retry);
    }
  }
  function refreshTree() {if(workspace){const expanded=new Set([...el('fileTree').querySelectorAll('details[open]')].map(node=>node.dataset.path));return loadTree(el('fileTree'),'',workspace,++treeGeneration,expanded);}}
  function setWorkspace(root) {
    if(root===workspace)return;
    if(el('quickOpenDialog').open)el('quickOpenDialog').close();
    syncGeneration++;clearTimeout(syncTimer);selection++;activate(null);workspace=root;el('editorWorkspace').textContent=root;el('editorWorkspace').title=root;
    activate([...files.values()].find(f=>f.workspace===root)||null);
    notice([...files.values()].some(f=>f.workspace!==root&&dirty(f))?'其他工作区的未保存编辑仍保留，切回可继续编辑。':'');
    el('fileTree').replaceChildren();
    gitGeneration++;updateGitCount(0);el('refreshGit').disabled=false;el('gitChanges').replaceChildren();el('gitBranch').textContent='';
    el('statusBranch').textContent='';el('statusBranch').hidden=true;
    syncWorkspace(root);
  }
  async function gitRequest(kind,params) {
    const response=await fetch('/api/git/'+kind+'?'+new URLSearchParams(params));
    const data=await response.json();
    if(!response.ok)throw Error(data.detail||'Git 读取失败');
    return data;
  }
  function updateGitCount(count) {
    const badge=el('gitChangeCount'),button=el('activityGit');
    badge.hidden=!count;badge.textContent=count?String(count):'';
    button.setAttribute('aria-label',count?'源代码管理，'+count+' 个改动文件':'源代码管理');
    button.title='源代码管理（只读）'+(count?' · '+count+' 个改动文件':'');
  }
  async function refreshGit() {
    if(!workspace)return;
    const root=workspace,ticket=++gitGeneration;
    el('gitNotice').textContent='正在读取 Git 状态…';el('refreshGit').disabled=true;
    el('gitChanges').replaceChildren();
    try {
      const data=await gitRequest('status',{workspace:root});
      if(ticket!==gitGeneration||root!==workspace)return;
      updateGitCount(data.repository?new Set(data.entries.map(entry=>entry.path)).size:0);
      el('gitBranch').textContent=data.repository?'⑂ '+data.branch+(data.detached?'（分离 HEAD）':''):'';
      el('gitBranch').title=el('gitBranch').textContent;
      el('statusBranch').textContent=el('gitBranch').textContent;
      el('statusBranch').title=root+' · '+el('gitBranch').textContent;
      el('statusBranch').hidden=!data.repository;
      el('gitNotice').textContent=!data.repository?'当前工作区不是 Git 仓库':data.entries.length?'':'没有改动，工作区干净';
      for(const [group,label] of [['conflicts','合并冲突'],['staged','已暂存的更改'],['unstaged','更改'],['untracked','未跟踪的文件']]) {
        const entries=data.entries.filter(entry=>entry.group===group);if(!entries.length)continue;
        const section=document.createElement('details'),summary=document.createElement('summary');section.open=true;
        summary.textContent=label+'  '+entries.length;section.append(summary);
        for(const entry of entries) {
          const button=document.createElement('button'),name=document.createElement('span'),status=document.createElement('span');
          button.type='button';button.className='git-file';button.dataset.group=group;button.dataset.path=entry.path;
          name.textContent=entry.path;status.textContent=entry.status;status.className='git-status git-status-'+entry.status.replace('?','new');
          button.title=entry.path+' · '+label;button.append(name,status);
          button.onclick=run(()=>openDiff(entry,root));section.append(button);
        }
        el('gitChanges').append(section);
      }
    } catch(error) {if(ticket===gitGeneration){updateGitCount(0);el('gitNotice').textContent=error.message;el('statusBranch').textContent='';el('statusBranch').hidden=true;}}
    finally {if(ticket===gitGeneration)el('refreshGit').disabled=false;}
  }
  function closeDiff() {
    if(!diffVisible)return;
    diffVisible=false;diffEditor?.setModel(null);
    for(const model of diffModels)model.dispose();diffModels=[];
    el('gitDiffPane').hidden=true;
  }
  async function openDiff(entry,root) {
    if(root!==workspace)return;
    const ticket=++selection;notice('正在读取差异…');
    const [data]=await Promise.all([gitRequest('diff',{workspace:root,path:entry.path,group:entry.group}),loadEditor()]);
    if(ticket!==selection||root!==workspace)return;
    if(active)active.view=editor.saveViewState();
    closeDiff();
    if(!diffEditor)diffEditor=monaco.editor.createDiffEditor(el('gitDiffHost'),{
      automaticLayout:true,readOnly:true,originalEditable:false,renderSideBySide:true,
      useInlineViewWhenSpaceIsLimited:false,enableSplitViewResizing:true,
      minimap:{enabled:false},fontSize:15,scrollBeyondLastLine:false,ignoreTrimWhitespace:false,
      smoothScrolling:false,cursorBlinking:'solid',cursorSmoothCaretAnimation:'off'
    });
    diffModels=[data.original,data.modified].map((content,index)=>monaco.editor.createModel(content,language(entry.path),monaco.Uri.from({
      scheme:'git-view',authority:encodeURIComponent(root),path:'/'+entry.path,query:entry.group+'-'+index
    })));
    diffEditor.setModel({original:diffModels[0],modified:diffModels[1]});diffVisible=true;
    el('gitDiffPane').hidden=false;el('gitDiffTitle').textContent=entry.path+' · 差异对比（只读）';
    el('gitOriginalLabel').textContent=data.labels[0];el('gitModifiedLabel').textContent=data.labels[1];
    workbench.showEditor();render();diffEditor.layout();
    notice('对比内容来自 Git 和磁盘；编辑器尚未保存的修改不包含在内。');
  }
  el('closeGitDiff').onclick=()=>{selection++;closeDiff();render();editor?.layout();notice('');};
  el('refreshGit').onclick=()=>{selection++;closeDiff();render();refreshGit();};
  el('saveFile').onclick=run(save);
  el('reloadFile').onclick=run(async()=>{const file=active;if(!file)return;if(dirty(file)&&!confirm('放弃当前文件未保存的修改，重新加载磁盘内容？'))return;await refreshFile(file.path,{discard:true});});
  el('refreshTree').onclick=refreshTree;
  new MutationObserver(()=>{if(monaco)monaco.editor.setTheme(editorTheme());}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  window.addEventListener('codex:workspace',event=>setWorkspace(event.detail.workspace));
  window.addEventListener('codex:files-changed',event=>{const root=event.detail.workspace;if(root!==workspace)return;clearTimeout(syncTimer);syncTimer=setTimeout(()=>run(()=>syncWorkspace(root))(),200);});
  window.addEventListener('beforeunload',event=>{if([...files.values()].some(dirty)){event.preventDefault();event.returnValue='';}});
  document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'&&active){event.preventDefault();run(save)();}});
  return {openFile,refreshFile,refreshTree,refreshGit,syncWorkspace,layout(){editor?.layout();diffEditor?.layout();}};
}
