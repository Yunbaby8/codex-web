// Offline interaction tests: no server, browser, account or model requests.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const read=name=>fs.readFileSync(path.join(__dirname,'../frontend',name),'utf8');
function element(){
  const classes=new Set(),props=new Map();
  return {dataset:{},attrs:{},children:[],offsetWidth:400,clientWidth:1000,
    classList:{contains:x=>classes.has(x),add:x=>classes.add(x),remove:x=>classes.delete(x),toggle(x,on=!classes.has(x)){on?classes.add(x):classes.delete(x);return on;}},
    style:{setProperty:(k,v)=>props.set(k,v),getPropertyValue:k=>props.get(k)||'',removeProperty:k=>props.delete(k)},
    setAttribute(k,v){this.attrs[k]=v;},append(...items){this.children.push(...items);items.forEach(x=>x.parentElement=this);},prepend(x){this.children.unshift(x);x.parentElement=this;},
    replaceChildren(){this.children=[];this.rebuilds=(this.rebuilds||0)+1;},setPointerCapture(){},focus(){}};
}
function workbench(storage=new Map(),narrow=false){
  const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  const ctx={$:get,localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)},
    document:{documentElement:element(),querySelectorAll:()=>[],querySelector:get,addEventListener(){}},
    matchMedia:()=>({matches:narrow,addEventListener(){}}),innerWidth:narrow?390:1440};
  vm.createContext(ctx);const source=read('workbench.js');vm.runInContext(source.slice(source.indexOf(String.fromCharCode(10))+1).replace('export function','function'),ctx);
  const api=ctx.createWorkbench({onSidebarCollapsed(){},refreshGit(){},layoutEditors(){}});
  return {get,storage,api};
}
{
  const {get}=workbench();
  for(const id of ['activitySessions','activityFiles','activityGit','activitySessions']){
    get(id).onclick();
    assert.equal(get('app').classList.contains('sidebar-collapsed'),false);
    assert.equal(get('workbench').style.getPropertyValue('--code-width'),'');
    assert.equal(get('workbench').classList.contains('code-hidden'),false);
    assert.equal(get(id).attrs['aria-pressed'],'true');
  }
  get('activitySessions').onclick();assert.equal(get('app').classList.contains('sidebar-collapsed'),true);
  get('activityFiles').onclick();assert.equal(get('app').classList.contains('sidebar-collapsed'),false);
}
{
  const {get,storage}=workbench();
  get('sessionResize').onkeydown({key:'ArrowRight',preventDefault(){}});
  assert.equal(storage.get('codexWebSidebarWidth'),'420');
  const handle=get('chatResize');handle.onpointerdown({button:0,clientX:100,pointerId:1,preventDefault(){}});
  handle.onpointermove({clientX:200});handle.onpointerup();assert.equal(storage.get('codexWebCodePercent'),'50');
  const restored=workbench(storage).get;
  assert.equal(restored('app').style.getPropertyValue('--sidebar-width'),'420px');
  assert.equal(restored('workbench').style.getPropertyValue('--code-width'),'50%');
  restored('chatResize').ondblclick();restored('sessionResize').ondblclick();assert.equal(storage.has('codexWebCodePercent'),false);assert.equal(storage.has('codexWebSidebarWidth'),false);
}
{
  const {get,api}=workbench(new Map(),true);assert.equal(get('app').classList.contains('sidebar-collapsed'),true);
  get('activityFiles').onclick();assert.equal(get('app').classList.contains('sidebar-collapsed'),false);
  assert.equal(get('workbench').classList.contains('code-hidden'),true);
  api.showEditor();assert.equal(get('app').classList.contains('sidebar-collapsed'),true);assert.equal(get('workbench').classList.contains('code-hidden'),false);
}
// Exercise the production close/render functions with disposable editor models.
const source=read('editor.js');
const closeCode=source.slice(source.indexOf('  function closeFile('),source.indexOf('  async function openFile('));
const renderCode=source.slice(source.indexOf('  function render('),source.indexOf('  function activate('));
function file(name){return {path:name,workspace:'root',model:{dispose(){}},listener:{dispose(){}},tabButton:element()};}
for(const [selected,expected] of [[1,2],[2,1],[0,1]]){
  const list=['a','b','c'].map(file),ctx={files:new Map(list.map(f=>[f.path,f])),workspace:'root',active:list[selected],dirty:()=>false,render(){},confirm:()=>true};
  ctx.activate=f=>ctx.active=f;vm.createContext(ctx);vm.runInContext(closeCode,ctx);ctx.closeFile(list[selected].path);assert.equal(ctx.active,list[expected]);assert.equal(ctx.files.size,2);
}
{
  const f=file('dirty'),ctx={files:new Map([['dirty',f]]),workspace:'root',active:f,dirty:()=>true,confirm:()=>false};
  vm.createContext(ctx);vm.runInContext(closeCode,ctx);ctx.closeFile('dirty');assert.equal(ctx.files.size,1);
}
{
  const nodes=new Map(),el=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  const f=file('a'),button=f.tabButton;button.textContent='a';
  const ctx={files:new Map([['a',f]]),workspace:'root',active:f,diffVisible:false,dirty:()=>true,el,updateEditorStatus(){}};
  vm.createContext(ctx);vm.runInContext(renderCode,ctx);ctx.render(false);ctx.render(false);
  assert.equal(f.tabButton,button);assert.equal(button.textContent,'● a');assert.equal(el('editorTabs').rebuilds,undefined);assert.equal(el('saveFile').disabled,false);
}
const html=read('index.html');
assert.equal((html.match(/id="fileExplorer"/g)||[]).length,1);
assert.ok(html.indexOf('id="sidebarPane"')<html.indexOf('id="fileExplorer"'));
assert.ok(html.indexOf('id="fileExplorer"')<html.indexOf('id="workbench"'));
assert.equal(html.includes('id="treeResize"'),false);
console.log('PASS: sidebar switching, collapse/mobile behavior, width persistence/reset, adjacent tab closing, unsaved cancellation, stable tab nodes, shared sidebar structure');
