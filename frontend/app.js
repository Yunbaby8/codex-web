import {fail} from './ui.js';
import {createWorkbench} from './workbench.js';
import {createEditor} from './editor.js';
import {createConversation} from './conversation.js';
import {createComposer} from './composer.js';
import {createSessions} from './sessions.js';

// Wire concrete feature controllers once; no shared writable state or import cycles.
export let sessions;
export const workbench=createWorkbench({
  onSidebarCollapsed:()=>sessions?.closeSessionMenu(),
  refreshGit:()=>editor.refreshGit(),
  layoutEditors:()=>editor.layout(),
});
export const editor=createEditor(workbench);
// Preserve the documented bridge for editor integrations.
window.codexEditor=editor;
export const conversation=createConversation({
  getCurrent:()=>sessions.current,
  getWorkspace:()=>sessions.workspace,
  getRoots:()=>sessions.config?.roots,
  setWorkspace:path=>sessions.setWorkspace(path),
  setBusy:value=>sessions.setBusy(value),
  openFile:(...args)=>editor.openFile(...args),
  rerunTurn:(...args)=>composer.rerunTurn(...args),
});
export const composer=createComposer({
  getCurrent:()=>sessions.current,
  getWorkspace:()=>sessions.workspace,
  getConfig:()=>sessions.config,
  isBusy:()=>sessions.busy,
  isSelecting:()=>sessions.selecting,
  newSession:(...args)=>sessions.newSession(...args),
  onSent:(...args)=>sessions.updateAfterSend(...args),
  conversation,workbench,
});
sessions=createSessions({conversation,composer,workbench});
sessions.boot().catch(error=>{
  const panel=document.getElementById('loginPanel');
  if(!panel.hidden)document.getElementById('loginError').textContent=error.message;
  else fail(error);
});
