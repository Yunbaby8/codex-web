import {$, api, fail, guarded} from './ui.js';

export function createConversation({getCurrent, getWorkspace, getRoots, setWorkspace, setBusy, openFile, rerunTurn}) {
  // Rendering and reading state stays private to this conversation view.
  const items=new Map(), turns=new Map(), pending=new Map(), readingViews=new Map();
  let followLatest=true, lastScrollTop=0, restoringView=false, contextUsage=null;
  function updateReadingPosition(){
    if(restoringView)return;
    const messages=$('messages');
    if(followLatest)messages.scrollTop=messages.scrollHeight;
    lastScrollTop=messages.scrollTop;
    $('latestMessage').hidden=followLatest&&messages.scrollHeight-messages.clientHeight-messages.scrollTop<=2;
  }
  $('messages').addEventListener('scroll',()=>{
    if(restoringView)return;
    const messages=$('messages'),top=messages.scrollTop;
    if(top<lastScrollTop-1)followLatest=false;
    else if(top>lastScrollTop+1&&messages.scrollHeight-messages.clientHeight-top<=2)followLatest=true;
    lastScrollTop=top;
    $('latestMessage').hidden=followLatest&&messages.scrollHeight-messages.clientHeight-top<=2;
  },{passive:true});
  $('messages').addEventListener('wheel',event=>{if(event.deltaY<0){followLatest=false;updateReadingPosition();}},{passive:true});
  $('messages').addEventListener('keydown',event=>{if(['ArrowUp','PageUp','Home'].includes(event.key)){followLatest=false;updateReadingPosition();}});
  $('messages').addEventListener('click',event=>{if(event.target.closest('summary')){followLatest=false;updateReadingPosition();}});
  document.addEventListener('selectionchange',()=>{const selection=getSelection();if(!selection.isCollapsed&&$('messages').contains(selection.anchorNode)){followLatest=false;updateReadingPosition();}});
  $('latestMessage').onclick=()=>{followLatest=true;updateReadingPosition();};
  $('messages').addEventListener('load',updateReadingPosition,true);
  new ResizeObserver(updateReadingPosition).observe($('messages'));
  function saveReadingView(){
    if(!getCurrent()||restoringView)return;
    const view={top:$('messages').scrollTop,follow:followLatest,details:Object.fromEntries([...$('messages').querySelectorAll('details[data-view-key]')].map(node=>[node.dataset.viewKey,node.open])),activityChoices:Object.fromEntries([...turns.values()].flatMap(turn=>turn.segments.map(segment=>[segment.activity.dataset.viewKey,segment.activityManual])))};
    readingViews.set(getCurrent().id,view);
    try{sessionStorage.setItem('codexWebView:'+getCurrent().id,JSON.stringify(view));}catch{/* Keep the in-memory view if browser storage is full. */}
  }
  const stripAnsi = s => String(s || '').replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g,'').replace(/\x1b\[[0-?]*[ -/]*[@-~]/g,'');
  function formatTokenCount(value){
    const amount=Math.max(0,Number(value)||0),units=[['M',1000000],['k',1000]];
    for(const [suffix,unit] of units)if(amount>=unit){const digits=amount>=unit*100?0:amount>=unit*10?1:2;return (amount/unit).toFixed(digits).replace(/\.0+$|(?<=\.\d)0+$/,'')+suffix;}
    return Math.round(amount).toLocaleString('en-US');
  }
  function renderContextUsage(){
    const indicator=$('contextUsage'),ring=$('contextUsageRing');
    if(!indicator||!ring)return;
    const used=Number(contextUsage?.last?.totalTokens),limit=Number(contextUsage?.modelContextWindow);
    indicator.classList.remove('warning','danger');
    if(!Number.isFinite(used)||!Number.isFinite(limit)||limit<=0){
      ring.style.setProperty('--context-progress','0%');indicator.title='上下文用量未知';indicator.setAttribute('aria-label','上下文用量未知');return;
    }
    const ratio=Math.max(0,used/limit),progress=Math.min(1,ratio),shown=ratio>1?'>100%':Math.round(ratio*100)+'%',description='上下文：'+formatTokenCount(used)+' / '+formatTokenCount(limit)+' tokens（'+shown+'）';
    ring.style.setProperty('--context-progress',(progress*100)+'%');indicator.classList.toggle('warning',ratio>=.75&&ratio<.9);indicator.classList.toggle('danger',ratio>=.9);indicator.title=description;indicator.setAttribute('aria-label',description);
  }
  function setContextUsage(usage){contextUsage=usage||null;renderContextUsage();}
  function itemText(item){
    switch(item.type){
      case 'userMessage': return (item.content||[]).map(c=>c.text||'').join('\n');
      case 'agentMessage': return item.text||'';
      case 'commandExecution': return (item.command||'')+'\n'+(item.aggregatedOutput||'')+(item.exitCode!=null?'\n[exit '+item.exitCode+']':'');
      case 'reasoning': return [...(item.summary||[]),...(item.content||[])].join('\n');
      case 'fileChange': return (item.changes||[]).map(c=>c.path+'\n'+(c.diff||'')).join('\n');
      default: return JSON.stringify(item,null,2);
    }
  }
  function formatDuration(milliseconds){
    const seconds=Math.max(0,Math.round(milliseconds/1000));
    const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;
    return (hours?hours+'小时 ':'')+(minutes?minutes+'分钟 ':'')+rest+'秒';
  }
  function ensureTurn(turn){
    const id=typeof turn==='string'?turn:turn.id;
    let entry=turns.get(id);
    if(!entry){
      $('empty')?.remove();
      const node=document.createElement('section'),user=document.createElement('div'),status=document.createElement('div'),duration=document.createElement('span'),answer=document.createElement('article'),role=document.createElement('div'),answerBody=document.createElement('div'),answerActions=document.createElement('div'),thinking=document.createElement('div'),thinkingText=document.createElement('span');
      node.className='turn';node.dataset.turnId=id;user.className='turn-user';status.className='turn-status';status.hidden=true;duration.className='turn-duration';duration.setAttribute('aria-live','off');status.append(duration);answer.className='message assistant';answer.hidden=true;role.className='role';role.textContent='Codex';answerBody.className='markdown assistant-markdown';answerActions.className='message-actions';
      thinking.className='turn-thinking';thinking.hidden=true;thinking.setAttribute('role','status');thinking.setAttribute('aria-live','polite');thinkingText.textContent='正在思考';thinking.append(thinkingText);
      answer.append(role,answerBody,answerActions);node.append(user,thinking,status,answer);$('messages').append(node);
      entry={node,user,status,answer,answerBody,answerActions,thinking,duration,id,startedAt:null,running:false,segments:[],agentItems:new Map(),userItems:new Map(),workItems:new Map()};turns.set(id,entry);
      appendActivitySegment(entry,'turn:'+id);
    }
    if(typeof turn==='object'&&turn.startedAt!=null)entry.startedAt=turn.startedAt*1000;
    return entry;
  }
  function appendActivitySegment(turn,key){
    const activity=document.createElement('details'),activitySummary=document.createElement('summary'),activityList=document.createElement('div');
    activity.className='turn-activity';activity.hidden=true;activity.open=turn.running;activity.dataset.viewKey=key;activityList.className='activity-list';activity.append(activitySummary,activityList);
    turn.node.insertBefore(activity,turn.thinking);
    const segment={activity,activitySummary,activityList,activityManual:false,fileItems:new Map(),fileNode:null,planNode:null};
    turn.segments.push(segment);
    activitySummary.onclick=()=>{segment.activityManual=true;};
    activity.addEventListener('toggle',updateReadingPosition);
    return segment;
  }
  function renderTurnDuration(entry){
    if(entry.startedAt==null)return;
    const elapsed=entry.durationMs!=null?entry.durationMs:Date.now()-entry.startedAt;
    entry.duration.textContent=(entry.turnStatus==='failed'?'任务失败':entry.turnStatus==='interrupted'?'已停止':'已处理')+' · '+formatDuration(elapsed);
    entry.status.hidden=entry.running;
    updateActivity(entry);
  }
  function updateTurn(turn,running){
    const entry=ensureTurn(turn);entry.running=running;entry.turnStatus=turn.status;
    if(running&&entry.startedAt==null)entry.startedAt=Date.now();
    if(!running&&entry.startedAt!=null&&entry.durationMs==null)entry.durationMs=Date.now()-entry.startedAt;
    entry.failed=Boolean(turn.error||turn.status==='failed'||turn.status==='interrupted');
    for(const segment of entry.segments){
      if(!segment.activityManual){
        if(running)segment.activity.open=true;
        else if(followLatest&&!segment.activity.contains(document.activeElement)&&!segment.activity.contains(getSelection().anchorNode))segment.activity.open=false;
      }
      if(entry.failed)segment.activity.open=true;
    }
    entry.thinking.hidden=!running;
    if(turn.durationMs!=null)entry.durationMs=turn.durationMs;
    else if(!running&&turn.completedAt!=null&&entry.startedAt!=null)entry.durationMs=turn.completedAt*1000-entry.startedAt;
    updateActivity(entry);renderAnswer(entry);
    renderTurnDuration(entry);
  }
  setInterval(()=>{for(const entry of turns.values())if(entry.running)renderTurnDuration(entry);},1000);
  function workspaceFileReference(target,root=getCurrent()?.cwd||getWorkspace()){
    if(!root||!target||/^[a-z][a-z0-9+.-]*:/i.test(target)&&!/^.*:\d+(?::\d+)?$/.test(target))return null;
    let path=target,line=null,column=1;
    const location=path.match(/^(.*?)(?::(\d+)(?::(\d+))?|#L(\d+)(?:C(\d+))?)$/);
    if(location){path=location[1];line=Number(location[2]||location[4]);column=Number(location[3]||location[5]||1);}
    root=root.replace(/\/$/,'')||'/';
    if(path.startsWith('/')){const prefix=root==='/'?'/':root+'/';if(!path.startsWith(prefix))return null;path=path.slice(prefix.length);}
    else path=path.replace(/^\.\//,'');
    if(!path||path.includes('\0')||path.includes('\\')||path.split('/').some(part=>!part||part==='..'||part==='.')||path.includes('://'))return null;
    return {workspace:root,path,line,column};
  }
  function localFileHref(target){
    const reference=workspaceFileReference(target);
    if(reference)return '/api/files?path='+encodeURIComponent((reference.workspace==='/'?'':reference.workspace)+'/'+reference.path)+(reference.line?'#L'+reference.line:'');
    let path=target,line='';const location=target.match(/^(.*):(\d+)(?::\d+)?$/);
    if(location){path=location[1];line='#L'+location[2];}
    if(!getRoots()?.some(root=>path===root||path.startsWith(root+'/')))return null;
    return '/api/files?path='+encodeURIComponent(path)+line;
  }
  function markEditorLink(link,target){
    const reference=workspaceFileReference(target);if(!reference)return;
    link.dataset.editorWorkspace=reference.workspace;link.dataset.editorPath=reference.path;
    if(reference.line)link.dataset.editorLine=reference.line;
    link.dataset.editorColumn=reference.column;link.title=reference.path+(reference.line?':'+reference.line:'');
  }
  $('messages').addEventListener('click',guarded(async event=>{
    const link=event.target.closest('a[data-editor-path]');if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    event.preventDefault();const root=link.dataset.editorWorkspace;
    if(getWorkspace()!==root)setWorkspace(root);
    await openFile(link.dataset.editorPath,{line:link.dataset.editorLine?Number(link.dataset.editorLine):null,column:Number(link.dataset.editorColumn)||1});
  }));
  function updateFilePathTooltip(event,path){
    const tooltip=$('filePathTooltip');
    if(!event||!path){tooltip.hidden=true;return;}
    tooltip.textContent=path;tooltip.hidden=false;
    const gap=14,margin=10;
    let left=event.clientX+gap,top=event.clientY+gap;
    if(left+tooltip.offsetWidth>window.innerWidth-margin)left=event.clientX-tooltip.offsetWidth-gap;
    if(top+tooltip.offsetHeight>window.innerHeight-margin)top=event.clientY-tooltip.offsetHeight-gap;
    tooltip.style.left=Math.max(margin,left)+'px';tooltip.style.top=Math.max(margin,top)+'px';
  }
  function copyText(text,button){
    const done=()=>{button.classList.add('copied');button.title='已复制';setTimeout(()=>{button.classList.remove('copied');button.title='复制';},1200);};
    if(navigator.clipboard?.writeText)return navigator.clipboard.writeText(text).then(done);
    const textarea=document.createElement('textarea');textarea.value=text;textarea.className='copy-source';document.body.append(textarea);textarea.select();const copied=document.execCommand('copy');textarea.remove();if(!copied)throw Error('浏览器不允许复制，请手动选择内容');done();return Promise.resolve();
  }
  function copyButton(text,label='复制'){
    const button=document.createElement('button');button.type='button';button.className='copy-button';button.title=label;button.setAttribute('aria-label',label);button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg><span>'+label+'</span>';button.onclick=guarded(()=>copyText(text,button));return button;
  }
  function actionButton(label,icon,action){
    const button=document.createElement('button');button.type='button';button.className='answer-action';button.title=label;button.setAttribute('aria-label',label);button.innerHTML=icon;button.onclick=guarded(()=>action(button));return button;
  }
  function appendInline(container,value){
    const text=String(value||''),pattern=/(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|\[[^\]\n]+\]\((?:<[^>\n]+>|https?:\/\/[^)\s]+|[^)\n]+)\)|https?:\/\/[^\s<]+|(?:\.?\/)?[\w.-]+(?:\/[\w.-]+)*\.[a-zA-Z0-9]+:\d+(?::\d+)?)/g;
    let cursor=0;
    for(const match of text.matchAll(pattern)){
      container.append(document.createTextNode(text.slice(cursor,match.index)));const token=match[0];
      if(token.startsWith('`')){const code=document.createElement('code'),target=token.slice(1,-1);code.textContent=target;if(/^(?:[^\s]+\.[a-zA-Z0-9]+|(?:[^\s]+\/)?(?:Makefile|Dockerfile))(?::\d+(?::\d+)?|#L\d+(?:C\d+)?)?$/.test(target)&&workspaceFileReference(target)){const link=document.createElement('a');link.href=localFileHref(target);markEditorLink(link,target);link.append(code);container.append(link);}else container.append(code);}
      else if(token.startsWith('**')||token.startsWith('__')){const strong=document.createElement('strong');appendInline(strong,token.slice(2,-2));container.append(strong);}
      else if(token.startsWith('*')||token.startsWith('_')){const emphasis=document.createElement('em');appendInline(emphasis,token.slice(1,-1));container.append(emphasis);}
      else{
        const markdown=token.match(/^\[([^\]]+)\]\((.+)\)$/),raw=(markdown?markdown[2]:token).replace(/^<|>$/g,''),trailing=markdown?'':(raw.match(/[),.;!?，。；：！？]+$/)?.[0]||''),target=trailing?raw.slice(0,-trailing.length):raw,href=/^https?:\/\//.test(target)?target:localFileHref(target);
        if(href){const link=document.createElement('a');link.textContent=markdown?markdown[1]:target;link.href=href;markEditorLink(link,target);link.target='_blank';link.rel='noopener noreferrer';container.append(link);if(trailing)container.append(document.createTextNode(trailing));}
        else if(markdown&&target.startsWith('/')){const unavailable=document.createElement('span');unavailable.className='local-link-unavailable';unavailable.textContent=markdown[1];unavailable.title='文件不在允许访问的目录内，无法从网页打开';unavailable.setAttribute('role','link');unavailable.setAttribute('aria-disabled','true');container.append(unavailable);}
        else container.append(document.createTextNode(token));
      }
      cursor=match.index+token.length;
    }
    container.append(document.createTextNode(text.slice(cursor)));
  }
  function isTableDivider(line){return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);}
  function tableCells(line){return line.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(cell=>cell.trim());}
  function normalizedLanguage(language){
    const value=String(language||'').toLowerCase();
    return ({cxx:'cpp','c++':'cpp',cc:'cpp',h:'cpp',hpp:'cpp',py:'python',js:'javascript',jsx:'javascript',ts:'typescript',tsx:'typescript',sh:'shell',bash:'shell',zsh:'shell',yml:'yaml'}[value]||value);
  }
  function highlightCode(code,text,language){
    const lang=normalizedLanguage(language);code.replaceChildren();
    if(lang==='diff'||lang==='patch'){
      String(text).split(/(?<=\n)/).forEach(line=>{const span=document.createElement('span');span.className=line.startsWith('+')&&!line.startsWith('+++')?'token-add':line.startsWith('-')&&!line.startsWith('---')?'token-delete':line.startsWith('@@')?'token-hunk':'token-plain';span.textContent=line;code.append(span);});return;
    }
    const strings='"(?:\\\\.|[^"\\\\])*"|\'(?:\\\\.|[^\'\\\\])*\'',number='\\b(?:0x[\\da-fA-F]+|\\d+(?:\\.\\d+)?)\\b';let patterns=[];
    if(['cpp','c','javascript','typescript','java','css'].includes(lang))patterns=[['comment','\\/\\*[\\s\\S]*?\\*\\/|\\/\\/[^\\n]*'],['string',strings],['keyword','\\b(?:alignas|auto|bool|break|case|catch|class|const|constexpr|continue|default|delete|do|else|enum|export|extends|false|finally|float|for|function|if|implements|import|in|instanceof|int|interface|let|namespace|new|null|nullptr|private|protected|public|return|sizeof|static|struct|switch|template|this|throw|true|try|typedef|typename|undefined|using|var|virtual|void|while)\\b'],['number',number],['keyword','^[ \\t]*#[^\\n]*']];
    else if(lang==='python')patterns=[['comment','#[^\\n]*'],['string','"""[\\s\\S]*?"""|\'\'\'[\\s\\S]*?\'\'\'|'+strings],['keyword','\\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|False|finally|for|from|global|if|import|in|is|lambda|None|nonlocal|not|or|pass|raise|return|True|try|while|with|yield)\\b'],['number',number]];
    else if(lang==='shell')patterns=[['comment','#[^\\n]*'],['string',strings],['keyword','\\b(?:case|do|done|elif|else|esac|export|fi|for|function|if|in|local|readonly|select|then|until|while)\\b'],['variable','\\$\\{?[A-Za-z_][A-Za-z0-9_]*\\}?'],['number',number]];
    else if(lang==='json')patterns=[['string',strings],['keyword','\\b(?:true|false|null)\\b'],['number',number]];
    else if(lang==='yaml')patterns=[['comment','#[^\\n]*'],['string',strings],['keyword','\\b(?:true|false|null|yes|no|on|off)\\b'],['number',number]];
    if(!patterns.length||String(text).length>150000){code.textContent=text;return;}
    const matcher=new RegExp(patterns.map(pattern=>'('+pattern[1]+')').join('|'),'gm');let cursor=0,match;
    while((match=matcher.exec(text))){code.append(document.createTextNode(text.slice(cursor,match.index)));const index=match.slice(1).findIndex(value=>value!==undefined),span=document.createElement('span');span.className='token-'+patterns[index][0];span.textContent=match[0];code.append(span);cursor=matcher.lastIndex;}
    code.append(document.createTextNode(text.slice(cursor)));
  }
  function renderCodeBlock(container,language,codeText){
    const block=document.createElement('div'),header=document.createElement('div'),lang=document.createElement('span'),pre=document.createElement('pre'),code=document.createElement('code');
    block.className='code-block';header.className='code-block-header';lang.textContent=language||'代码';if(language)code.dataset.language=language;highlightCode(code,codeText,language);header.append(lang,copyButton(codeText));pre.append(code);block.append(header,pre);container.append(block);
    header.querySelector('button').onclick=guarded(event=>copyText(event.currentTarget.closest('.code-block').querySelector('code').textContent,event.currentTarget));
  }
  // Patch changed text in place so completed paragraphs, selection and code scroll survive streaming.
  function syncMarkdownChildren(container,rendered){
    const next=[...rendered.childNodes];
    next.forEach((node,index)=>{
      const old=container.childNodes[index];
      if(!old){container.append(node);return;}
      if(old.isEqualNode(node))return;
      if(old.nodeType!==node.nodeType||old.nodeName!==node.nodeName){old.replaceWith(node);return;}
      if(old.nodeType===Node.TEXT_NODE){
        let prefix=0;while(prefix<old.length&&prefix<node.length&&old.data[prefix]===node.data[prefix])prefix++;
        old.replaceData(prefix,old.length-prefix,node.data.slice(prefix));return;
      }
      for(const attr of [...old.attributes])if(!node.hasAttribute(attr.name))old.removeAttribute(attr.name);
      for(const attr of node.attributes)if(old.getAttribute(attr.name)!==attr.value)old.setAttribute(attr.name,attr.value);
      syncMarkdownChildren(old,node);
    });
    while(container.childNodes.length>next.length)container.lastChild.remove();
  }
  function renderMarkdown(container,value){
    const target=container;container=document.createDocumentFragment();const lines=stripAnsi(value).replace(/\r\n?/g,'\n').split('\n');let i=0;
    while(i<lines.length){
      const line=lines[i];if(!line.trim()){i++;continue;}
      const fence=line.match(/^\s*(`{3,})\s*([^\s`]*)\s*$/);
      if(fence){const codeLines=[],closing=new RegExp('^\\s*`{'+fence[1].length+',}\\s*$');i++;while(i<lines.length&&!closing.test(lines[i]))codeLines.push(lines[i++]);if(i<lines.length)i++;renderCodeBlock(container,fence[2],codeLines.join('\n'));continue;}
      const heading=line.match(/^\s*(#{1,6})\s+(.+)$/);
      if(heading){const element=document.createElement('h'+heading[1].length);appendInline(element,heading[2]);container.append(element);i++;continue;}
      if(/^\s*(---+|___+|\*\*\*+)\s*$/.test(line)){container.append(document.createElement('hr'));i++;continue;}
      if(i+1<lines.length&&line.includes('|')&&isTableDivider(lines[i+1])){
        const wrap=document.createElement('div'),table=document.createElement('table'),thead=document.createElement('thead'),headRow=document.createElement('tr');wrap.className='table-wrap';
        for(const cell of tableCells(line)){const th=document.createElement('th');appendInline(th,cell);headRow.append(th);}thead.append(headRow);table.append(thead);i+=2;const tbody=document.createElement('tbody');
        while(i<lines.length&&lines[i].includes('|')&&lines[i].trim()){const row=document.createElement('tr');for(const cell of tableCells(lines[i])){const td=document.createElement('td');appendInline(td,cell);row.append(td);}tbody.append(row);i++;}
        table.append(tbody);wrap.append(table);container.append(wrap);continue;
      }
      const listMatch=line.match(/^(\s*)([-+*]|\d+\.)\s+(.+)$/);
      if(listMatch){
        const ordered=/\d+\./.test(listMatch[2]),indent=listMatch[1].length,list=document.createElement(ordered?'ol':'ul');
        if(ordered&&parseInt(listMatch[2],10)!==1)list.start=parseInt(listMatch[2],10);
        while(i<lines.length){
          const listItem=lines[i].match(/^(\s*)([-+*]|\d+\.)\s+(.+)$/);
          if(!listItem||listItem[1].length!==indent||/\d+\./.test(listItem[2])!==ordered)break;
          const li=document.createElement('li'),parts=[listItem[3]],contentIndent=listItem[0].length-listItem[3].length;i++;
          while(i<lines.length){
            if(!lines[i].trim()){
              let next=i+1;while(next<lines.length&&!lines[next].trim())next++;
              const sibling=lines[next]?.match(/^(\s*)([-+*]|\d+\.)\s+(.+)$/);
              if(sibling&&sibling[1].length===indent&&/\d+\./.test(sibling[2])===ordered){i=next;break;}
              if(next>=lines.length||lines[next].match(/^\s*/)[0].length<contentIndent)break;
              parts.push('');i=next;continue;
            }
            if(lines[i].match(/^\s*/)[0].length<contentIndent)break;
            parts.push(lines[i].slice(contentIndent));i++;
          }
          if(parts.length===1)appendInline(li,parts[0]);else renderMarkdown(li,parts.join('\n'));
          list.append(li);
        }
        container.append(list);continue;
      }
      if(/^\s*>/.test(line)){const quote=document.createElement('blockquote'),parts=[];while(i<lines.length&&/^\s*>/.test(lines[i]))parts.push(lines[i++].replace(/^\s*>\s?/,''));renderMarkdown(quote,parts.join('\n'));container.append(quote);continue;}
      const paragraph=document.createElement('p'),parts=[line.trim()];i++;
      while(i<lines.length&&lines[i].trim()&&!/^\s*```/.test(lines[i])&&!/^\s*#{1,6}\s+/.test(lines[i])&&!/^\s*([-+*]|\d+\.)\s+/.test(lines[i])&&!/^\s*>/.test(lines[i])&&!(i+1<lines.length&&lines[i].includes('|')&&isTableDivider(lines[i+1]))){parts.push(lines[i].trim());i++;}
      parts.forEach((part,index)=>{if(index)paragraph.append(document.createTextNode(' '));appendInline(paragraph,part);});container.append(paragraph);
    }
    syncMarkdownChildren(target,container);
  }
  function normalizeFinalAnswer(text){
    return String(text||'').replace(/^(\s*)本轮新增(?:读取|核对)\s*[：:]/,'$1本轮已读：');
  }
  function combinedAnswer(turn){
    const text=[...turn.agentItems.values()].filter(item=>item.phase!=='commentary').map(itemText).filter(Boolean).join('\n\n');
    return normalizeFinalAnswer(text);
  }
  function renderAnswer(turn){
    const text=combinedAnswer(turn);
    turn.answer.hidden=!text;
    renderMarkdown(turn.answerBody,text);renderAnswerActions(turn);if(turn.running)renderTurnDuration(turn);
  }
  function feedbackKey(turn){return 'codexWebFeedback:'+getCurrent()?.id+':'+turn.id;}
  function setFeedback(turn,value){const key=feedbackKey(turn),previous=localStorage.getItem(key);if(previous===value)localStorage.removeItem(key);else localStorage.setItem(key,value);renderAnswerActions(turn);}
  function speakAnswer(text,button){
    if(!('speechSynthesis' in window))throw Error('当前浏览器不支持朗读');
    if(window.speechSynthesis.speaking){window.speechSynthesis.cancel();button.classList.remove('active');button.title='朗读回答';return;}
    const utterance=new SpeechSynthesisUtterance(text);utterance.lang='zh-CN';button.classList.add('active');button.title='停止朗读';utterance.onend=utterance.onerror=()=>{button.classList.remove('active');button.title='朗读回答';};window.speechSynthesis.speak(utterance);
  }
  function renderAnswerActions(turn){
    turn.answerActions.replaceChildren();const text=combinedAnswer(turn);if(!text||turn.running)return;
    const copy=copyButton(text,'复制回答'),speak=actionButton('朗读回答','<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 10v4h3l4 4V6L8 10H5Z"/><path d="M15 9c1.2 1.5 1.2 4.5 0 6m2.5-8.5c2.7 3 2.7 8 0 11"/></svg>',button=>speakAnswer(text,button)),up=actionButton('回答有帮助（仅保存在本机）','<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 21H4V9h3m0 12h10.2a2 2 0 0 0 2-1.6l1.4-7A2 2 0 0 0 18.7 10H14l1-4.2A2.3 2.3 0 0 0 12.8 3L7 9v12Z"/></svg>',()=>setFeedback(turn,'up')),down=actionButton('回答没帮助（仅保存在本机）','<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3H4v12h3m0-12h10.2a2 2 0 0 1 2 1.6l1.4 7a2 2 0 0 1-1.9 2.4H14l1 4.2a2.3 2.3 0 0 1-2.2 2.8L7 15V3Z"/></svg>',()=>setFeedback(turn,'down')),rerun=actionButton('再次运行','<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.3-5.7L20 8.5M20 4v4.5h-4.5"/></svg>',button=>rerunTurn([...turn.userItems.values()],button)),feedback=localStorage.getItem(feedbackKey(turn));
    copy.classList.add('answer-action');if(feedback==='up'){up.classList.add('active');up.setAttribute('aria-pressed','true');}if(feedback==='down'){down.classList.add('active');down.setAttribute('aria-pressed','true');}turn.answerActions.append(copy,speak,up,down,rerun);
  }
  function updateActivity(turn){
    for(const segment of turn.segments){
      const placeholder=segment.activityList.querySelector('.activity-placeholder');
      if(placeholder)placeholder.hidden=[...segment.activityList.children].some(node=>node!==placeholder&&!node.hidden);
      const count=[...segment.activityList.children].filter(node=>!node.hidden).length;
      segment.activity.hidden=count===0;
      segment.activitySummary.textContent=(turn.running?'工作过程':'查看处理过程')+' · '+count+' 项';
    }
    turn.status.hidden=turn.running||!turn.duration.textContent;
    let label='正在思考';
    const active=[...turn.workItems.values()].filter(entry=>!entry.completed&&entry.item.status!=='completed'&&entry.item.status!=='failed'&&entry.item.status!=='declined');
    const waiting=[...pending.values()].find(req=>req.params?.turnId===turn.id);
    const latest=active.at(-1)?.item;
    if(waiting)label=waiting.method.endsWith('requestApproval')?'等待操作许可':'等待你的回复';
    else if(latest?.type==='commandExecution')label=commandLabel(latest);
    else if(latest?.type==='fileChange')label='正在修改文件';
    else if(latest?.type==='agentMessage')label=latest.phase==='commentary'?'正在说明进度':'正在回复';
    else if(latest&&latest.type!=='reasoning')label=toolLabel(latest);
    turn.thinking.firstChild.textContent=label;
    turn.thinking.hidden=!turn.running||(!waiting&&latest?.type==='agentMessage'&&latest.phase!=='commentary');
  }
  function commandLabel(item){
    if(item.status==='failed'||(item.exitCode!=null&&item.exitCode!==0))return '执行失败';
    if(item.status==='declined')return '已拒绝';
    const actions=item.commandActions||[],running=item.status==='inProgress';
    if(actions.length&&actions.every(action=>['read','search','listFiles'].includes(action.type))){
      if(actions.every(action=>action.type==='read'))return running?'正在读取文件':'已读取文件';
      if(actions.every(action=>action.type==='search'))return running?'正在搜索':'已搜索';
      return running?'正在浏览代码':'已浏览代码';
    }
    return running?'正在执行命令':'已执行命令';
  }
  function toolLabel(item){
    const labels={webSearch:'搜索网页',mcpToolCall:'调用工具',dynamicToolCall:'调用工具',imageView:'查看图片',contextCompaction:'整理上下文',collabToolCall:'协作任务',plan:'方案'};
    return labels[item.type]||'工具调用';
  }
  function renderPlan(turn,plan){
    const segment=turn.segments.at(-1);
    if(!segment.planNode){segment.planNode=document.createElement('section');segment.planNode.className='turn-plan';segment.activityList.append(segment.planNode);}
    const title=document.createElement('strong');title.textContent='任务计划';segment.planNode.replaceChildren(title);
    if(plan.explanation){const note=document.createElement('p');note.textContent=plan.explanation;segment.planNode.append(note);}
    const list=document.createElement('ul');
    for(const step of plan.plan||[]){const row=document.createElement('li');row.dataset.status=step.status;const marker=document.createElement('span');marker.textContent=step.status==='completed'?'✓':step.status==='inProgress'?'◉':'○';marker.setAttribute('aria-label',step.status==='completed'?'已完成':step.status==='inProgress'?'进行中':'待开始');row.append(marker,document.createTextNode(step.step));list.append(row);}
    segment.planNode.append(list);updateActivity(turn);
  }
  function createActivityEntry(){const node=document.createElement('details'),summary=document.createElement('summary'),label=document.createElement('span'),meta=document.createElement('span'),body=document.createElement('div');node.className='activity-entry';label.className='activity-label';meta.className='activity-meta';body.className='activity-body';summary.append(label,meta);node.append(summary,body);return{node,summary,label,meta,body};}
  function renderUnifiedDiff(diff){
    const view=document.createElement('div'),table=document.createElement('table'),body=document.createElement('tbody');view.className='diff-view';table.className='diff-table';let oldLine=null,newLine=null;
    for(const rawLine of stripAnsi(diff).replace(/\r\n?/g,'\n').split('\n')){
      const hunk=rawLine.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/),row=document.createElement('tr');
      if(hunk){oldLine=Number(hunk[1]);newLine=Number(hunk[2]);row.className='diff-hunk';const cell=document.createElement('td');cell.colSpan=3;cell.textContent='@@ -'+oldLine+' +'+newLine+' @@'+hunk[3];row.append(cell);body.append(row);continue;}
      if(/^(diff --git|index |--- |\+\+\+ )/.test(rawLine)){row.className='diff-meta';const cell=document.createElement('td');cell.colSpan=3;cell.textContent=rawLine;row.append(cell);body.append(row);continue;}
      let kind='context',oldNumber=oldLine,newNumber=newLine,text=rawLine;
      if(rawLine.startsWith('+')){kind='add';oldNumber='';text=rawLine.slice(1);if(newLine!=null)newLine++;}
      else if(rawLine.startsWith('-')){kind='delete';newNumber='';text=rawLine.slice(1);if(oldLine!=null)oldLine++;}
      else if(rawLine.startsWith(' ')){text=rawLine.slice(1);if(oldLine!=null)oldLine++;if(newLine!=null)newLine++;}
      else if(rawLine.startsWith('\\ No newline')){kind='meta';oldNumber='';newNumber='';}
      else{kind='meta';oldNumber='';newNumber='';}
      row.className='diff-'+kind;
      for(const number of [oldNumber,newNumber]){const cell=document.createElement('td');cell.className='diff-line-number';cell.textContent=number==null?'':number;row.append(cell);}
      const content=document.createElement('td');content.className='diff-line-code';content.textContent=(kind==='add'?'+':kind==='delete'?'-':kind==='context'?' ':'')+text;row.append(content);body.append(row);
    }
    table.append(body);view.append(table);return view;
  }
  function combinedFileChange(turn,latestCompleted){
    const files=new Map();let running=false;
    for(const item of turn.fileItems.values()){
      if(item.status==='inProgress')running=true;
      for(const change of item.changes||[]){
        const path=String(change.path||''),diff=String(change.diff||'');
        if(!files.has(path))files.set(path,{path,diffs:[],seen:new Set()});
        const file=files.get(path);if(diff&&!file.seen.has(diff)){file.seen.add(diff);file.diffs.push(diff);}
      }
    }
    return {type:'fileChange',status:running||!latestCompleted?'inProgress':'completed',changes:[...files.values()].map(file=>({path:file.path,diff:file.diffs.join('\n')}))};
  }
  function renderFileChange(entry,item,completed){
    const changes=item.changes||[],card=document.createElement('details'),header=document.createElement('summary'),heading=document.createElement('div'),title=document.createElement('strong'),review=document.createElement('button'),files=document.createElement('div'),full=document.createElement('div');
    card.className='file-change-card';header.className='file-change-header';heading.className='file-change-heading';files.className='changed-files';full.className='file-change-diff';full.hidden=true;
    heading.innerHTML='<span class="file-change-icon" aria-hidden="true">✎</span>';
    const done=item.status?item.status==='completed':completed;
    title.textContent=(done?'编辑了 ':'正在编辑 ')+changes.length+' 个文件';heading.append(title);
    review.type='button';review.className='review-change';review.textContent='查看更改 ↗';review.onclick=()=>{full.hidden=!full.hidden;review.textContent=full.hidden?'查看更改 ↗':'收起更改'};
    header.append(heading);card.append(header);
    for(const [index,change] of changes.entries()){
      const row=document.createElement('div'),path=document.createElement('span'),stats=document.createElement('span');
      row.className='changed-file';if(index>=3)row.hidden=true;
      const absolute=String(change.path||''),label=getCurrent()?.cwd&&absolute.startsWith(getCurrent().cwd+'/')?absolute.slice(getCurrent().cwd.length+1):absolute,href=localFileHref(absolute);
      if(href){const link=document.createElement('button');link.type='button';link.className='file-link';link.textContent=label;link.onclick=guarded(async()=>{if(getCurrent()?.cwd&&absolute.startsWith(getCurrent().cwd+'/')){if(getWorkspace()!==getCurrent().cwd)setWorkspace(getCurrent().cwd);await openFile(absolute.slice(getCurrent().cwd.length+1));}else window.open(href,'_blank','noopener,noreferrer');});link.onmouseenter=e=>updateFilePathTooltip(e,absolute);link.onmousemove=e=>updateFilePathTooltip(e,absolute);link.onmouseleave=()=>updateFilePathTooltip();path.append(link);}else path.textContent=label;
      let added=0,removed=0;
      for(const line of String(change.diff||'').split('\n')){if(line.startsWith('+')&&!line.startsWith('+++'))added++;else if(line.startsWith('-')&&!line.startsWith('---'))removed++;}
      stats.className='change-stats';stats.innerHTML='<span class="added">+'+added+'</span> <span class="removed">-'+removed+'</span>';
      row.append(path,stats);files.append(row);
      const diffFile=document.createElement('section'),diffHeading=document.createElement('div');diffFile.className='diff-file';diffHeading.className='diff-file-heading';diffHeading.textContent=label;diffFile.append(diffHeading,renderUnifiedDiff(change.diff||''));full.append(diffFile);
    }
    if(changes.length>3){
      const more=document.createElement('button');more.type='button';more.className='more-files';more.textContent='再显示 '+(changes.length-3)+' 个文件⌄';
      more.onclick=()=>{const hidden=files.querySelector('.changed-file[hidden]');for(const row of files.querySelectorAll('.changed-file'))row.hidden=!hidden&&row.dataset.extra==='true';more.textContent=hidden?'收起文件⌃':'再显示 '+(changes.length-3)+' 个文件⌄'};
      for(const row of [...files.children].slice(3))row.dataset.extra='true';files.append(more);
    }
    card.append(files,review,full);entry.node.replaceChildren(card);
  }
  function upsert(item,turnId,completed=false){
    if(!item.id)return;
    const turn=ensureTurn(turnId||'standalone-'+item.id);let entry=items.get(item.id);
    if(!entry){
      $('empty')?.remove();
      let segment=turn.segments.at(-1);
      if(item.type==='userMessage'){
        let host=turn.user;
        if(turn.userItems.size||item.webSteer){
          // Keep earlier work above the new instruction, including when no
          // reasoning summary was provided by the server.
          if(![...segment.activityList.children].some(node=>!node.hidden)){
            const note=document.createElement('div');note.className='progress-message activity-placeholder';note.textContent='此前处理过程暂无可显示内容';segment.activityList.append(note);
          }
          host=document.createElement('div');host.className='turn-steers';turn.node.insertBefore(host,turn.thinking);
          segment=appendActivitySegment(turn,'steer:'+item.id);
        }
        const node=document.createElement('article'),body=document.createElement('div');node.className='message user';body.className='markdown user-markdown';node.append(body);host.append(node);entry={node,body,item,turn};turn.userItems.set(item.id,item);
      }else if(item.type==='reasoning'){
        const activity=createActivityEntry();activity.node.classList.add('reasoning-entry');activity.node.dataset.viewKey='item:'+item.id;activity.body.classList.add('markdown');segment.activityList.append(activity.node);entry={...activity,item,turn};
      }else if(item.type==='agentMessage'&&item.phase==='commentary'){
        const node=document.createElement('div'),body=document.createElement('div');node.className='progress-message';body.className='markdown';node.append(body);segment.activityList.append(node);entry={node,body,item,turn,progress:true};if(item.type==='agentMessage')turn.agentItems.set(item.id,item);
      }else if(item.type==='agentMessage'){
        entry={node:turn.answer,item,turn};turn.agentItems.set(item.id,item);
      }else if(item.type==='fileChange'){
        if(!segment.fileNode){segment.fileNode=document.createElement('article');segment.fileNode.className='message file-change';segment.activityList.append(segment.fileNode);}
        entry={node:segment.fileNode,item,turn};
      }else{
        const activity=createActivityEntry();activity.node.dataset.viewKey='item:'+item.id;
        let host=segment.activityList;
        if(item.type==='commandExecution'&&item.commandActions?.length&&item.commandActions.every(action=>['read','search','listFiles'].includes(action.type))){
          let group=host.lastElementChild;
          if(!group?.classList.contains('exploration-group')){group=document.createElement('details');group.className='exploration-group';group.dataset.viewKey='exploration:'+item.id;group.append(document.createElement('summary'),document.createElement('div'));host.append(group);}
          host=group.lastElementChild;
        }
        host.append(activity.node);entry={...activity,item,turn};
      }
      entry.segment=segment;items.set(item.id,entry);
    }
    entry.item=item;entry.completed=completed;if(item.type!=='userMessage')turn.workItems.set(item.id,entry);if(item.type==='agentMessage')turn.agentItems.set(item.id,item);if(item.type==='userMessage')turn.userItems.set(item.id,item);if(item.type==='fileChange')entry.segment.fileItems.set(item.id,item);
    if(item.type==='fileChange')renderFileChange(entry,combinedFileChange(entry.segment,completed),completed);
    else if(item.type==='commandExecution'){
      entry.node.dataset.status=item.status|| (completed?'completed':'inProgress');
      if(item.status==='failed'||(item.exitCode!=null&&item.exitCode!==0)){entry.node.dataset.status='failed';entry.node.open=true;}
      const command=stripAnsi(item.command||'').replace(/\s+/g,' ').trim(),output=stripAnsi(item.aggregatedOutput||'').trimEnd();entry.label.textContent=commandLabel(item);entry.meta.textContent=command.length>80?command.slice(0,80)+'…':command;entry.body.replaceChildren();const pre=document.createElement('pre');pre.textContent=output+(item.exitCode!=null?'\n[exit '+item.exitCode+']':'')||'暂无输出';entry.body.append(pre);
    }else if(item.type==='reasoning'){
      entry.label.textContent=completed?'思考摘要':'正在思考';entry.meta.textContent='';entry.node.hidden=!itemText(item).trim();renderMarkdown(entry.body,itemText(item));
    }else if(item.type==='agentMessage'&&entry.progress)renderMarkdown(entry.body,itemText(item));
    else if(item.type==='agentMessage')renderAnswer(turn);
    else if(item.type==='userMessage')renderMarkdown(entry.body,itemText(item));
    else if(item.type!=='fileChange'){
      entry.label.textContent=toolLabel(item);entry.meta.textContent=item.status==='failed'?'失败':completed?'完成':'进行中';entry.node.dataset.status=item.status|| (completed?'completed':'inProgress');if(item.status==='failed')entry.node.open=true;entry.body.replaceChildren();if(item.type==='plan'){entry.body.classList.add('markdown');renderMarkdown(entry.body,item.text||'');}else{const pre=document.createElement('pre');pre.textContent=stripAnsi(itemText(item));entry.body.append(pre);}
    }
    entry.node.querySelector('.sent-images')?.remove();
    if(item.type==='userMessage'){
      const pictures=document.createElement('div');pictures.className='sent-images';
      for(const content of item.content||[]){
        const match=(content.path||'').match(/\/uploads\/([0-9a-f]{32})\//);
        if(content.type==='localImage'&&match){const img=document.createElement('img');img.onerror=()=>img.replaceWith(document.createTextNode('图片已清理或不可用'));img.src=`/api/sessions/${getCurrent().id}/attachments/${match[1]}`;img.alt='已发送图片';pictures.append(img);}
      }
      if(pictures.childNodes.length)entry.node.append(pictures);
    }
    const group=entry.node.closest('.exploration-group');
    if(group){const nodes=[...group.lastElementChild.children],running=nodes.some(node=>node.dataset.status==='inProgress'),failed=nodes.some(node=>node.dataset.status==='failed');group.firstChild.textContent=(running?'正在浏览代码':'已浏览代码')+' · '+nodes.length+' 项'+(failed?' · 存在失败':'');if(failed)group.open=true;}
    updateActivity(turn);
  }
  function renderRequests(){
    $('requests').replaceChildren();
    for(const [id,req] of pending){
      const box=document.createElement('div');box.className='request';box.dataset.request=id;
      const title=document.createElement('strong');title.textContent=req.method.endsWith('requestApproval')?'Codex 请求操作许可':'Codex 需要补充信息';box.append(title);
      if(req.method.endsWith('requestApproval')){
        const reason=document.createElement('p');reason.textContent=req.params.reason||'请确认是否允许这次操作';box.append(reason);
        if(req.params.command){const command=document.createElement('pre');command.textContent=req.params.command;box.append(command);}
        const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='操作详情';pre.textContent=JSON.stringify(req.params,null,2);details.append(summary,pre);box.append(details);
        for(const [label,decision] of [['允许本次','accept'],['拒绝','decline'],['取消任务','cancel']]){
          const button=document.createElement('button');button.textContent=label;button.onclick=guarded(async()=>{await api(`/sessions/${getCurrent().id}/requests/${encodeURIComponent(id)}`,'POST',{decision});pending.delete(id);renderRequests();});box.append(button);
        }
      }else{
        const button=document.createElement('button');button.textContent='打开提问';
        button.onclick=()=>showQuestion(id,req);box.append(button);
      }
      $('requests').append(box);
    }
    const dialog=$('questionDialog'),next=[...pending].find(([,req])=>!req.method.endsWith('requestApproval'));
    if(dialog.dataset.session!==getCurrent()?.id||!pending.has(dialog.dataset.request)){
      dialog.close();delete dialog.dataset.request;delete dialog.dataset.session;
      if(next)showQuestion(...next);
    }
    updateQuestionCards();
    for(const turn of turns.values())updateActivity(turn);
  }
  function updateQuestionCards(){
    const dialog=$('questionDialog');
    for(const box of $('requests').children)box.hidden=dialog.open&&box.dataset.request===dialog.dataset.request;
  }
  function showQuestion(id,req){
    const dialog=$('questionDialog'),sid=getCurrent().id;
    if(dialog.dataset.request===id&&dialog.dataset.session===sid){if(!dialog.open)dialog.show();updateQuestionCards();return;}
    dialog.dataset.request=id;dialog.dataset.session=sid;
    $('questionFields').replaceChildren();$('questionError').textContent='';$('submitQuestion').disabled=false;
    const inputs=[];
    for(const [index,q] of req.params.questions.entries()){
      const field=document.createElement('fieldset'),legend=document.createElement('legend');
      legend.textContent=(q.header?q.header+' · ':'')+q.question;field.append(legend);
      const choices=[];
      for(const option of q.options||[]){
        const label=document.createElement('label');label.className='question-option';
        const radio=document.createElement('input');radio.type='radio';radio.name='question-'+index;radio.value=option.label;
        const text=document.createElement('span'),name=document.createElement('strong');name.textContent=option.label;text.append(name);
        if(option.description){const description=document.createElement('span');description.textContent=option.description;text.append(description);}
        label.append(radio,text);field.append(label);choices.push(radio);
      }
      const label=document.createElement('label');label.className='question-custom';label.textContent=choices.length?'其他（自行填写）':'你的回答';
      const input=document.createElement('input');input.type=q.isSecret?'password':'text';input.autocomplete='off';
      input.placeholder=choices.length?'输入自己的答案':'请输入回答';
      input.oninput=()=>{if(input.value.trim())for(const radio of choices)radio.checked=false;};
      for(const radio of choices)radio.onchange=()=>{input.value='';};
      label.append(input);field.append(label);$('questionFields').append(field);inputs.push({id:q.id,input,choices});
    }
    $('questionForm').onsubmit=async event=>{
      event.preventDefault();const button=$('submitQuestion');if(button.disabled)return;
      const answers={};
      for(const {id:key,input,choices} of inputs){
        const answer=input.value.trim()||choices.find(radio=>radio.checked)?.value;
        if(!answer){$('questionError').textContent='请回答每个问题后再提交。';(choices[0]||input).focus();return;}
        answers[key]=answer;
      }
      button.disabled=true;$('questionError').textContent='';
      try{
        await api(`/sessions/${sid}/requests/${encodeURIComponent(id)}`,'POST',{answers});
        if(getCurrent()?.id===sid&&pending.get(id)===req){pending.delete(id);renderRequests();}
      }catch(error){
        if(dialog.dataset.request===id&&dialog.dataset.session===sid)$('questionError').textContent=error.message;
      }finally{
        if(dialog.dataset.request===id&&dialog.dataset.session===sid)button.disabled=false;
      }
    };
    if(!dialog.open)dialog.show();
    updateQuestionCards();
    $('questionFields').querySelector('input')?.focus();
  }
  $('closeQuestion').onclick=()=>$('questionDialog').close();
  $('questionDialog').addEventListener('close',updateQuestionCards);
  $('questionDialog').addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();$('questionDialog').close();$('message').focus();}
  });
  function handleEvent(event){
    const p=event.params||{}, method=event.method;
    if(method==='web/history'){
      items.clear();turns.clear();$('messages').replaceChildren();
      for(const turn of p.turns||[]){for(const item of turn.items||[])upsert(item,turn.id,turn.status!=='inProgress'||item.status==='completed');if(turn.webPlan)renderPlan(ensureTurn(turn.id),turn.webPlan);updateTurn(turn,turn.status==='inProgress');}
    }else if(method==='web/state'){
      $('error').textContent='';setBusy(p.active);pending.clear();for(const req of p.requests)pending.set(String(req.id),req);renderRequests();
    }else if(method==='item/started'||method==='item/completed'){upsert(p.item,p.turnId,method==='item/completed');if(!restoringView&&getCurrent()&&method==='item/completed'&&p.item.type==='fileChange')window.dispatchEvent(new CustomEvent('codex:files-changed',{detail:{workspace:getCurrent().cwd}}));
    }else if(method==='turn/plan/updated'){
      renderPlan(ensureTurn(p.turnId),p);
    }else if(method==='item/reasoning/summaryTextDelta'||method==='item/reasoning/summaryPartAdded'){
      const item=items.get(p.itemId)?.item||{id:p.itemId,type:'reasoning',summary:[],content:[]};
      const index=p.summaryIndex??0;item.summary||=[];
      if(Number.isInteger(index)&&index>=0&&index<10000){while(item.summary.length<=index)item.summary.push('');if(method.endsWith('summaryTextDelta'))item.summary[index]+=p.delta||'';upsert(item,p.turnId);}
    }else if(method==='item/plan/delta'){
      const item=items.get(p.itemId)?.item||{id:p.itemId,type:'plan',text:''};item.text+=p.delta;upsert(item,p.turnId);
    }else if(method==='item/agentMessage/delta'){
      const item=items.get(p.itemId)?.item||{id:p.itemId,type:'agentMessage',text:''};item.text+=p.delta;upsert(item,p.turnId);
    }else if(method==='item/commandExecution/outputDelta'){
      const item=items.get(p.itemId)?.item||{id:p.itemId,type:'commandExecution',command:'',aggregatedOutput:''};item.aggregatedOutput=(item.aggregatedOutput||'')+p.delta;upsert(item,p.turnId);
    }else if(method==='web/steer'){upsert(p.item,p.turnId,true);
    }else if(method==='turn/started'){updateTurn(p.turn,true);setBusy(true);
    }else if(method==='turn/completed'){updateTurn(p.turn,false);setBusy(false);if(!restoringView&&getCurrent())window.dispatchEvent(new CustomEvent('codex:files-changed',{detail:{workspace:getCurrent().cwd}}));for(const [id,req] of pending)if(req.method!=='web/asyncUserInput')pending.delete(id);renderRequests();if(p.turn.error)fail(Error(p.turn.error.message));if(p.turn.status==='interrupted')$('status').textContent='已停止';
    }else if(method==='thread/tokenUsage/updated'){if(p.threadId===getCurrent()?.id)setContextUsage(p.tokenUsage);
    }else if(method==='web/request'){pending.set(String(p.id),p);renderRequests();
    }else if(method==='web/requestResolved'){pending.delete(String(p.id));renderRequests();
    }else if(method==='error'||method==='web/error'){fail(Error(p.message||p.error?.message||JSON.stringify(p)));}
    updateReadingPosition();
  }

  function reset(){
    items.clear();turns.clear();pending.clear();$('messages').replaceChildren();
    setContextUsage(null);renderRequests();followLatest=true;
  }
  function restoreSession(session){
    restoringView=true;
    try{
      let view=readingViews.get(session.id);
      if(!view){try{view=JSON.parse(sessionStorage.getItem('codexWebView:'+session.id));}catch{/* Ignore an obsolete saved view. */}}
      reset();followLatest=view?.follow??true;setContextUsage(session.tokenUsage);
      if(session.history)handleEvent({method:'web/history',params:session.history});
      else for(const event of session.historyEvents||[])handleEvent(event);
      handleEvent({method:'web/state',params:{active:session.active,requests:session.requests||[]}});
      if(view?.details)$('messages').querySelectorAll('details[data-view-key]').forEach(node=>{const open=view.details[node.dataset.viewKey];if(typeof open==='boolean')node.open=open;});
      if(view)for(const turn of turns.values())for(const segment of turn.segments){segment.activityManual=Boolean(view.activityChoices?.[segment.activity.dataset.viewKey]??view.activityChoices?.[turn.id]);if(turn.failed||turn.running&&!segment.activityManual)segment.activity.open=true;}
      if(!followLatest)$('messages').scrollTop=view?.top||0;
    }finally{restoringView=false;updateReadingPosition();}
  }
  window.addEventListener('pagehide',saveReadingView);
  return {handleEvent,restoreSession,reset,saveReadingView,setContextUsage,updateReadingPosition,renderMarkdown,workspaceFileReference,
    follow(){followLatest=true;updateReadingPosition();},
    forgetSession(id){readingViews.delete(id);sessionStorage.removeItem('codexWebView:'+id);}};
}
