// Run the real page script against a small DOM fixture, without a browser or OBS.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, 'mobile.html'), 'utf8');
function element() {
  const classes = new Set();
  return {textContent:'',checked:false,disabled:false,style:{},dataset:{},listeners:{},
    classList:{add:key=>classes.add(key),remove:(...keys)=>keys.forEach(key=>classes.delete(key)),toggle:(key,on)=>on?classes.add(key):classes.delete(key)},
    addEventListener(type, callback) {this.listeners[type]=callback;},setAttribute(){},lastElementChild:{textContent:''}};
}
const ids = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(match=>[match[1],element()]));
const buttons = ['start','pause','resume','stop'].map(action=>Object.assign(element(),{dataset:{action}}));
const local = new Map(), session = new Map([['obs-token','test-pairing-token']]);
const storage = map=>({getItem:key=>map.get(key)||null,setItem:(key,value)=>map.set(key,value)});
const state = {instance_id:'test-server',revision:1,data_age_ms:0,connected:true,stale:false,active:true,paused:false,bytes:1048576,duration_text:'00:01:23',size_text:'1.00 MB',size_exact_text:'1,048,576 字节',state_text:'录制中',threshold_bytes:2147483648,alert_enabled:true,sound_enabled:true,alerted:false,busy:false,message:'',allowed:{start:false,pause:true,resume:false,stop:true}};
const requests = [];
const meta = {};
const context = vm.createContext({
  document:{getElementById:id=>ids[id],documentElement:{dataset:{}},querySelector:()=>meta,querySelectorAll:()=>buttons,addEventListener(){}},
  window:{addEventListener(){}},performance:{now:()=>0},setInterval:()=>1,
  localStorage:storage(local),sessionStorage:storage(session),URLSearchParams,AbortController,
  location:{hash:'',pathname:'/'},history:{replaceState(){}},setTimeout:()=>1,clearTimeout(){},
  fetch:async(url,options)=>{
    requests.push({url,options});
    let value=state;
    if(url==='/api/control') value={ok:true,message:'结束录制成功',state:{...state,active:false,state_text:'准备就绪',allowed:{start:true,stop:false,pause:false,resume:false}}};
    if(url==='/api/sound') value={ok:true,state:{...state,sound_enabled:false}};
    return {ok:true,status:200,json:async()=>value};
  }
});

(async()=>{
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(context.document.documentElement.dataset.theme,'light');
  const before=requests.length;
  ids.theme.listeners.click();
  assert.equal(context.document.documentElement.dataset.theme,'dark');
  assert.equal(local.get('obs-theme'),'dark');
  assert.equal(requests.length,before,'theme switch must not send recording commands');
  ids.theme.listeners.click();
  assert.equal(context.document.documentElement.dataset.theme,'light');
  assert.equal(buttons.find(b=>b.dataset.action==='start').disabled,true);
  assert.equal(buttons.find(b=>b.dataset.action==='pause').disabled,false);
  assert.equal(ids.exactSize.textContent,'1,048,576 字节');
  vm.runInContext('render('+JSON.stringify({...state,stale:true})+')',context);
  assert.ok(buttons.every(b=>b.disabled),'stale OBS sample must disable controls even with an open connection');
  assert.ok(ids.exactSize.textContent.includes('上次有效数据'));
  vm.runInContext('render('+JSON.stringify({...state,busy:true})+')',context);
  assert.ok(buttons.every(b=>b.disabled),'all controls disabled while another client command is pending');
  vm.runInContext('render('+JSON.stringify(state)+')',context);
  await buttons.find(b=>b.dataset.action==='stop').listeners.click();
  const control=requests.find(r=>r.url==='/api/control');
  assert.equal(JSON.parse(control.options.body).action,'stop');
  assert.equal(ids.state.textContent,'准备就绪');
  assert.equal(buttons.find(b=>b.dataset.action==='start').disabled,false);
  ids.sound.checked=false;
  await ids.sound.listeners.change();
  assert.equal(JSON.parse(requests.find(r=>r.url==='/api/sound').options.body).enabled,false);
  assert.equal(ids.sound.checked,false);
  vm.runInContext('offline("Wi-Fi disconnected")',context);
  assert.ok(buttons.every(b=>b.disabled));
  assert.equal(ids.state.textContent,'状态已过期');
  assert.equal(ids.sound.disabled,true);
  console.log('PASS: light/dark persistence, theme isolation, state-driven controls, busy state, stop request, sound toggle, stale-state protection');
})().catch(error=>{console.error(error);process.exitCode=1;});
