import {$, api, fail, guarded} from './ui.js';

export function createComposer({getCurrent, getWorkspace, getConfig, isBusy, isSelecting, newSession, onSent, conversation, workbench}) {
  let draftFiles=[], submitting=false, draftRevision=0;
  let fastRequested=localStorage.getItem('codexWebFastMode')==='true';
  const composerDrafts=new Map();
  function resizeComposer(){
    const input=$('message');input.style.height='auto';input.style.height=Math.min(240,Math.max(66,input.scrollHeight))+'px';
    conversation.updateReadingPosition();
  }
  function saveDraft(){
    const key=getCurrent()?.id||'new',text=$('message').value;
    composerDrafts.set(key,{text,files:draftFiles});
    try{if(text)sessionStorage.setItem('codexWebDraft:'+key,text);else sessionStorage.removeItem('codexWebDraft:'+key);}catch{/* The current page still retains the draft. */}
  }
  function restoreDraft(){
    const key=getCurrent()?.id||'new',draft=composerDrafts.get(key);
    $('message').value=draft?.text??sessionStorage.getItem('codexWebDraft:'+key)??'';draftFiles=draft?.files||[];
    draftRevision++;renderDraftFiles();resizeComposer();
  }
  $('message').addEventListener('input',()=>{draftRevision++;saveDraft();resizeComposer();});
  window.addEventListener('codex:ask-selection',event=>{
    const context=event.detail;
    if(isSelecting()){fail(Error('正在切换会话，请稍后再添加选中代码'));return;}
    if(context.workspace!==getWorkspace()||getCurrent()&&getCurrent().cwd!==context.workspace){fail(Error('选中代码与当前会话的工作区不同，请先切换到对应会话或新建会话'));return;}
    const blocks=context.selections.map(selection=>{
      const fence='`'.repeat([...selection.content.matchAll(/`+/g)].reduce((length,match)=>Math.max(length,match[0].length+1),3));
      const lines=selection.startLine===selection.endLine?'第 '+selection.startLine+' 行':'第 '+selection.startLine+'–'+selection.endLine+' 行';
      return '文件：'+context.path+'（'+lines+'，起始列 '+selection.startColumn+'）\n'+fence+context.language+'\n'+selection.content+'\n'+fence;
    });
    const draft=$('message').value;
    const addition='选中代码上下文\n工作区：'+context.workspace+'\n来源：'+(context.unsaved?'编辑器当前内容，包含尚未保存的修改':'编辑器当前内容')+'\n\n'+blocks.join('\n\n');
    const next=draft?(draft+'\n\n'+addition):(addition+'\n\n我的问题：');
    if(next.length>100000){fail(Error('选中代码和草稿合计超过消息长度上限，请缩小选区后再试'));return;}
    $('message').value=next;$('error').textContent='';
    workbench.showChat({focus:true});
    $('message').dispatchEvent(new Event('input'));$('message').focus();$('message').setSelectionRange(next.length,next.length);
  });
  function updateControls() {
    $('stop').disabled=!isBusy();$('stop').hidden=!isBusy();$('send').hidden=false;$('send').disabled=submitting||isSelecting();
    $('status').textContent=isBusy()?'Codex 正在工作，可继续引导…':'就绪';
    $('message').placeholder=isBusy()?'补充指令，引导当前正在执行的任务…':'让 Codex 帮你分析项目、修改代码或执行任务…';
    $('send').title=isBusy()?'引导当前任务（Enter），Shift + Enter 换行':'发送（Enter），Shift + Enter 换行';
    $('send').setAttribute('aria-label',isBusy()?'引导当前任务':'发送');$('composer').classList.toggle('steering',isBusy());updateFastControl();
  }
  function fastTier(){
    const model=getConfig()?.models.find(m=>m.id===$('model').value);
    return model?.service_tiers?.find(t=>t.name?.toLowerCase()==='fast'||['fast','priority'].includes(t.id))||null;
  }
  function updateFastControl(){
    const button=$('fastMode'),tier=fastTier(),active=Boolean(tier&&fastRequested&&!isBusy());
    button.disabled=!tier||isBusy();button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
    button.title=isBusy()?'执行中不能切换加速模式':tier?(tier.description||'Fast 模式速度更快，会增加额度消耗'):'当前模型不支持 Fast 模式';
  }
  function selectedServiceTier(){return fastRequested&&fastTier()?fastTier().id:'default';}
  function fillEfforts(preferred) {
    const model=getConfig().models.find(m=>m.id===$('model').value);
    const rank={low:0,medium:1,high:2,xhigh:3,max:4,ultra:5};
    const efforts=[...model.efforts].sort((a,b)=>(rank[b]??-1)-(rank[a]??-1));
    $('effort').replaceChildren(...efforts.map(e=>new Option(({low:'Low',medium:'Medium',high:'High',xhigh:'Extra High',max:'Max',ultra:'Ultra'})[e]||e,e)));
    $('effort').value=model.efforts.includes(preferred)?preferred:model.default_effort;
  }
  $('model').onchange=()=>{fillEfforts($('effort').value);updateFastControl();};
  $('fastMode').onclick=()=>{if(!fastTier()||isBusy())return;fastRequested=!fastRequested;localStorage.setItem('codexWebFastMode',String(fastRequested));updateFastControl();};
  async function rerunTurn(userItems,button){
    if(isBusy()||submitting)throw Error('Codex 正在工作，请等待当前任务结束');
    const message=userItems.map(item=>(item.content||[]).map(content=>content.text||'').join('\n')).filter(Boolean).join('\n\n');
    if(!message)throw Error('没有可再次运行的文字消息');
    if(userItems.some(item=>(item.content||[]).some(content=>content.type==='localImage')))throw Error('这轮包含附件，请从输入框重新发送');
    if(!window.confirm('再次运行会创建新一轮任务，也可能再次修改文件。确定继续吗？'))return;
    submitting=true;button.disabled=true;
    try{await api(`/sessions/${getCurrent().id}/messages`,'POST',{message,attachments:[],model:$('model').value,effort:$('effort').value,service_tier:selectedServiceTier()});}
    finally{submitting=false;if(!isBusy())button.disabled=false;}
  }
  $('composer').onsubmit=guarded(async e=>{
    e.preventDefault();if(submitting)return;
    if(isSelecting())throw Error('正在打开会话，请稍后发送');
    const sentText=$('message').value,message=sentText.trim(),sentRevision=draftRevision;if(!message&&!draftFiles.length)return;
    const steering=isBusy();
    conversation.follow();
    submitting=true;$('send').disabled=true;
    try{
      if(!getCurrent()){if(steering)throw Error('当前没有可引导的任务');await newSession(true);}
      if(!steering&&getCurrent().cwd!==getWorkspace())throw Error('工作目录已改变，请先点击 New Session');
      const sid=getCurrent().id;
      const uploaded=[];
      for(const draft of draftFiles){
        if(!draft.uploaded || draft.session!==sid){
          const response=await fetch(`/api/sessions/${sid}/attachments?name=${encodeURIComponent(draft.file.name)}`,{method:'POST',headers:{'X-Codex-Web':'1'},body:draft.file});
          const result=await response.json();if(!response.ok)throw Error(result.detail||'上传失败');
          draft.uploaded=result.id;draft.session=sid;
        }
        uploaded.push(draft.uploaded);
      }
      const result=steering?await api(`/sessions/${sid}/steer`,'POST',{message,attachments:uploaded}):await api(`/sessions/${sid}/messages`,'POST',{message,attachments:uploaded,model:$('model').value,effort:$('effort').value,service_tier:selectedServiceTier()});
      for(const draft of draftFiles)if(draft.preview)URL.revokeObjectURL(draft.preview);
      draftFiles=[];renderDraftFiles();
      if(draftRevision===sentRevision&&$('message').value===sentText)$('message').value='';
      saveDraft();resizeComposer();
      await onSent(sid,result,uploaded.length>0);
      if(steering)$('status').textContent='引导已发送，Codex 正在调整…';
    }finally{submitting=false;$('send').disabled=false;}
  });
  $('message').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('composer').requestSubmit();}};
  $('stop').onclick=guarded(async()=>{if(getCurrent())await api(`/sessions/${getCurrent().id}/stop`,'POST',{});});
  function renderDraftFiles(){
    $('attachments').replaceChildren();
    draftFiles.forEach((draft,index)=>{
      const chip=document.createElement('div');chip.className='attachment';
      if(draft.preview){const img=document.createElement('img');img.src=draft.preview;img.alt=draft.file.name;chip.append(img);}
      const name=document.createElement('span');name.textContent=draft.file.name+' · '+Math.ceil(draft.file.size/1024)+' KB';chip.append(name);
      const remove=document.createElement('button');remove.type='button';remove.className='quiet';remove.textContent='×';remove.setAttribute('aria-label','移除 '+draft.file.name);
      remove.onclick=()=>{if(submitting)return;if(draft.preview)URL.revokeObjectURL(draft.preview);draftFiles.splice(index,1);renderDraftFiles();};chip.append(remove);$('attachments').append(chip);
    });
  }
  function addFiles(files){
    if(submitting)return;
    for(const file of files){
      if(draftFiles.length>=8){fail(Error('每条消息最多 8 个附件'));break;}
      if(file.size>20*1024*1024){fail(Error(file.name+' 超过 20 MB'));continue;}
      draftFiles.push({file,preview:/^image\/(png|jpeg|webp|gif)$/.test(file.type)?URL.createObjectURL(file):null});
    }
    renderDraftFiles();
  }
  $('addFiles').onclick=()=>{if(!submitting)$('fileInput').click();};
  $('fileInput').onchange=()=>{addFiles($('fileInput').files);$('fileInput').value='';};
  $('message').addEventListener('paste',event=>{const files=Array.from(event.clipboardData?.files||[]);if(files.length){event.preventDefault();addFiles(files);}});
  $('composer').addEventListener('dragover',event=>{event.preventDefault();});
  $('composer').addEventListener('drop',event=>{event.preventDefault();addFiles(event.dataTransfer.files);});

  function forgetSession(id){
    for(const file of composerDrafts.get(id)?.files||[])if(file.preview)URL.revokeObjectURL(file.preview);
    composerDrafts.delete(id);sessionStorage.removeItem('codexWebDraft:'+id);
  }
  function adoptDraft(previousId){
    composerDrafts.delete(previousId);sessionStorage.removeItem('codexWebDraft:'+previousId);saveDraft();
  }
  window.addEventListener('pagehide',saveDraft);
  return {get submitting(){return submitting;},saveDraft,restoreDraft,adoptDraft,forgetSession,fillEfforts,updateFastControl,updateControls,rerunTurn};
}
