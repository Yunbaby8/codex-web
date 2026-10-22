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
  const savedView=localStorage.getItem('codexWebSidebarView');
  $('sidebarPane').dataset.view=['sessions','files','git'].includes(savedView)?savedView:'files';
  function setSidebarCollapsed(collapsed){
    $('app').classList.toggle('sidebar-collapsed',collapsed);$('sidebarPane').inert=collapsed;
    if(collapsed)onSidebarCollapsed();
    syncLayout();
  }
  setSidebarCollapsed(narrowWindow.matches||localStorage.getItem('codexWebSidebarHidden')==='true');
  narrowWindow.addEventListener('change',()=>setSidebarCollapsed(narrowWindow.matches||localStorage.getItem('codexWebSidebarHidden')==='true'));
  function syncActivityBar(){
    const view=$('sidebarPane').dataset.view,hidden=$('app').classList.contains('sidebar-collapsed');
    $('gitView').hidden=view!=='git';
    $('sidebar').inert=hidden||view!=='sessions';
    $('fileExplorer').inert=hidden||view==='sessions';
    for(const [id,name] of [['activitySessions','sessions'],['activityFiles','files'],['activityGit','git']])$(id).setAttribute('aria-pressed',String(!hidden&&view===name));
  }
  for(const [id,view] of [['activitySessions','sessions'],['activityFiles','files'],['activityGit','git']]){
    $(id).onclick=()=>{
      const collapsed=$('sidebarPane').dataset.view===view&&!$('app').classList.contains('sidebar-collapsed');
      $('sidebarPane').dataset.view=view;
      setSidebarCollapsed(collapsed);
      localStorage.setItem('codexWebSidebarView',view);
      if(!narrowWindow.matches)localStorage.setItem('codexWebSidebarHidden',String(collapsed));
      if(view==='git'&&!collapsed)refreshGit();
    };
  }
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
  function resize(handle,target,variable,min,max,measured,storageKey,ratio=false) {
    const saved=Number(localStorage.getItem(storageKey));
    if(saved>0&&Number.isFinite(saved))target.style.setProperty(variable,ratio?Math.min(85,Math.max(15,saved))+'%':Math.max(min,Math.min(max(),saved))+'px');
    const adjust=(base,delta)=>{
      const width=Math.max(min,Math.min(max(),base+delta));
      target.style.setProperty(variable,ratio?width/target.clientWidth*100+'%':width+'px');
    };
    const save=()=>localStorage.setItem(storageKey,parseFloat(target.style.getPropertyValue(variable)));
    handle.title='拖动调整宽度；双击恢复默认';
    handle.onpointerdown=event=>{
      if(event.button!==0)return;
      event.preventDefault();
      const base=measured.offsetWidth,start=event.clientX;
      handle.setPointerCapture(event.pointerId);
      handle.onpointermove=e=>adjust(base,e.clientX-start);
      handle.onpointerup=handle.onpointercancel=()=>{handle.onpointermove=null;save();};
    };
    handle.onkeydown=event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();adjust(measured.offsetWidth,event.key==='ArrowRight'?20:-20);save();};
    handle.ondblclick=()=>{target.style.removeProperty(variable);localStorage.removeItem(storageKey);layoutEditors();};
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
  resize($('sessionResize'),$('app'),'--sidebar-width',160,sidebarMax,$('sidebarPane'),'codexWebSidebarWidth');
  resize($('chatResize'),$('workbench'),'--code-width',320,()=>Math.max(320,$('workbench').clientWidth-360),$('codeArea'),'codexWebCodePercent',true);

  function syncLayout(){syncActivityBar();syncLayoutControls();}
  function showEditor(){ $('workbench').classList.remove('code-hidden');if(narrowWindow.matches)setSidebarCollapsed(true);else syncLayout(); }
  function showChat({focus=false}={}){
    $('workbench').classList.remove('chat-hidden');
    if(focus&&matchMedia('(max-width:1000px)').matches)$('workbench').classList.add('code-hidden');
    syncLayout();
  }
  syncLayout();
  return {showEditor,showChat,setSidebarCollapsed,collapseOnNarrowScreen(){if(narrowWindow.matches)setSidebarCollapsed(true);}};
}
