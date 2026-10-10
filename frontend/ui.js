export const $ = id => document.getElementById(id);
export async function api(path, method='GET', body) {
  const response = await fetch('/api'+path, {method,headers:{'Content-Type':'application/json','X-Codex-Web':'1'},body:body===undefined?undefined:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) { if(response.status===401) window.dispatchEvent(new Event('codex:login-required')); throw Error(data.detail || '请求失败'); }
  return data;
}
export function fail(error) { $('error').textContent = error.message; }
export function guarded(fn) { return async (...args) => { $('error').textContent=''; try { await fn(...args); } catch(e) {fail(e);} }; }
