import {$} from './ui.js';

export function createWorkbench({onSidebarCollapsed, refreshGit, layoutEditors}) {
  const themes = new Set(['tech','classic-black','white','green','red','yellow','beige']);
  function applyTheme(value) {
    if(value==='graphite')value='classic-black';
    const theme=themes.has(value)?value:'classic-black';
    document.documentElement.dataset.theme=theme;
    localStorage.setItem('codexWebWorkbenchTheme',theme);
    document.querySelectorAll('[data-theme-option]').forEach(button=>{
      const selected=button.dataset.themeOption===theme;
      button.classList.toggle('selected',selected);
      button.setAttribute('aria-checked',String(selected));
    });
  }
  applyTheme(localStorage.getItem('codexWebWorkbenchTheme'));
  const narrowWindow=matchMedia('(max-width:700px)');
  function setSidebarCollapsed(collapsed){
    $('app').classList.toggle('sidebar-collapsed',collapsed);$('sidebar').inert=collapsed;
    if(collapsed)onSidebarCollapsed();
    syncLayout();
  }
  setSidebarCollapsed(narrowWindow.matches||localStorage.getItem('codexWebWorkbenchSidebarCollapsed')!=='false');
  $('activitySessions').onclick=()=>{const collapsed=!$('app').classList.contains('sidebar-collapsed');setSidebarCollapsed(collapsed);if(!narrowWindow.matches)localStorage.setItem('codexWebWorkbenchSidebarCollapsed',String(collapsed));};
  narrowWindow.addEventListener('change',()=>setSidebarCollapsed(narrowWindow.matches||localStorage.getItem('codexWebWorkbenchSidebarCollapsed')!=='false'));
  function syncActivityBar(){
    const sessions=!$('app').classList.contains('sidebar-collapsed');
    $('gitView').hidden=!$('codeArea').classList.contains('git-mode');
    $('activitySessions').setAttribute('aria-pressed',String(sessions));
    $('activityFiles').setAttribute('aria-pressed',String(!sessions&&!$('workbench').classList.contains('code-hidden')&&!$('codeArea').classList.contains('explorer-hidden')&&!$('codeArea').classList.contains('git-mode')));
    $('activityGit').setAttribute('aria-pressed',String(!sessions&&!$('workbench').classList.contains('code-hidden')&&!$('codeArea').classList.contains('explorer-hidden')&&$('codeArea').classList.contains('git-mode')));
  }
  $('activityFiles').onclick=()=>{
    const showing=$('activityFiles').getAttribute('aria-pressed')==='true';
    setSidebarCollapsed(true);localStorage.setItem('codexWebWorkbenchSidebarCollapsed','true');
    $('workbench').classList.remove('code-hidden');
    $('codeArea').classList.remove('git-mode');
    $('codeArea').classList.toggle('explorer-hidden',showing);syncLayout();
  };
  $('activityGit').onclick=()=>{
    const showing=$('activityGit').getAttribute('aria-pressed')==='true';
    setSidebarCollapsed(true);localStorage.setItem('codexWebWorkbenchSidebarCollapsed','true');
    $('workbench').classList.remove('code-hidden');$('codeArea').classList.add('git-mode');
    $('codeArea').classList.toggle('explorer-hidden',showing);syncLayout();
    if(!showing)refreshGit();
  };
  $('activityChat').onclick=()=>{showChat();$('message').focus();};
  $('themeButton').onclick=e=>{
    e.stopPropagation();
    const menu=$('themeMenu'),open=menu.hidden;
    menu.hidden=!open;$('themeButton').setAttribute('aria-expanded',String(open));
  };
  $('themeMenu').onclick=e=>{
    const button=e.target.closest('[data-theme-option]');if(!button)return;
    applyTheme(button.dataset.themeOption);$('themeMenu').hidden=true;$('themeButton').setAttribute('aria-expanded','false');
  };
  document.addEventListener('click',e=>{
    if(!e.target.closest('.theme-control')){$('themeMenu').hidden=true;$('themeButton').setAttribute('aria-expanded','false');}
  });
  function resize(handle,target,variable,min,max,measured) {
    const adjust=(base,delta)=>target.style.setProperty(variable,Math.max(min,Math.min(max(),base+delta))+'px');
    handle.onpointerdown=event=>{
      if(event.button!==0)return;
      event.preventDefault();
      const base=measured.offsetWidth,start=event.clientX;
      handle.setPointerCapture(event.pointerId);
      handle.onpointermove=e=>adjust(base,e.clientX-start);
      handle.onpointerup=handle.onpointercancel=()=>{handle.onpointermove=null;};
    };
    handle.onkeydown=event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const base=measured.offsetWidth;adjust(base,event.key==='ArrowRight'?20:-20);};
  }
  function syncLayoutControls() {
    // The narrow-screen chat-only view still needs an accessible layout button.
    const host=$('workbench').classList.contains('code-hidden')?document.querySelector('.chat-toolbar'):$('editorHeader');
    if($('layoutControls').parentElement!==host){
      if(host===$('editorHeader'))host.append($('layoutControls'));
      else host.prepend($('layoutControls'));
    }
    const visible=!$('workbench').classList.contains('chat-hidden'),button=$('chatToggle');
    button.setAttribute('aria-expanded',String(visible));
    button.title=(visible?'隐藏':'显示')+'Codex 对话';button.setAttribute('aria-label',button.title);
  }
  $('chatToggle').onclick=()=>{
    const hidden=$('workbench').classList.toggle('chat-hidden');
    if(hidden)$('workbench').classList.remove('code-hidden');
    syncLayout();layoutEditors();
  };
  if(matchMedia('(max-width:1000px)').matches){$('workbench').classList.add('code-hidden');}
  const sidebarMax=()=>Math.min(innerWidth*.45,Math.max(160,innerWidth-650));
  resize($('treeResize'),$('app'),'--sidebar-width',160,sidebarMax,$('fileExplorer'));
  resize($('sessionResize'),$('app'),'--sidebar-width',160,sidebarMax,$('sidebar'));
  resize($('chatResize'),$('workbench'),'--code-width',320,()=>Math.max(320,$('workbench').clientWidth-360),$('codeArea'));

  function syncLayout(){syncActivityBar();syncLayoutControls();}
  function showEditor(){ $('workbench').classList.remove('code-hidden');syncLayout(); }
  function showChat({focus=false}={}){
    $('workbench').classList.remove('chat-hidden');
    if(focus&&matchMedia('(max-width:1000px)').matches)$('workbench').classList.add('code-hidden');
    syncLayout();
  }
  syncLayout();
  return {showEditor,showChat,setSidebarCollapsed,collapseOnNarrowScreen(){if(narrowWindow.matches)setSidebarCollapsed(true);}};
}
