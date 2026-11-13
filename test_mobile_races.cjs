const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = process.env.OBS_MOBILE_TEST_HTML || path.join(__dirname, 'mobile.html');
const html = fs.readFileSync(filename, 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const flush = () => new Promise(resolve => setImmediate(resolve));
const state = (revision = 1, paused = false, extra = {}) => ({instance_id:'test-server', revision, connected:true, stale:false,
  active:true, paused, bytes:1048576, data_age_ms:0, duration_text:'00:01:23', size_text:'1.00 MB',
  size_exact_text:'1,048,576 字节', state_text:paused?'已暂停':'录制中', threshold_bytes:2147483648,
  alert_enabled:true, sound_enabled:true, alerted:false, busy:false, message:'',
  allowed:{start:false,pause:!paused,resume:paused,stop:true}, ...extra});

function harness(storageBlocked = false, options = {}) {
  let clock = 0, wall = 100000, timerId = 0;
  const timers = new Map(), requests = [], errors = [], events = {}, documentEvents = {};
  function element() {
    const classes = new Set();
    return {textContent:'', checked:false, disabled:false, style:{}, dataset:{}, attributes:{}, listeners:{},
      classList:{add:k=>classes.add(k), remove:(...keys)=>keys.forEach(k=>classes.delete(k)),
        toggle:(k,on)=>on?classes.add(k):classes.delete(k), contains:k=>classes.has(k)},
      addEventListener(type,fn){this.listeners[type]=fn;}, setAttribute(k,v){this.attributes[k]=String(v);},
      lastElementChild:{textContent:''}};
  }
  const ids = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],element()]));
  const buttons = ['start','pause','resume','stop'].map(action=>Object.assign(element(),{dataset:{action}}));
  const local = options.local || new Map(), session = options.session || new Map();
  const storage = store => ({getItem(k){if(storageBlocked) throw Error('storage blocked');return store.get(k)||null;},
    setItem(k,v){if(storageBlocked) throw Error('storage blocked');store.set(k,v);},
    removeItem(k){if(storageBlocked) throw Error('storage blocked');store.delete(k);}});
  function schedule(fn, ms, interval = 0) {const id=++timerId;timers.set(id,{fn,at:clock+ms,interval});return id;}
  const document = {visibilityState:'visible', hidden:false, documentElement:{dataset:{}},
    getElementById:id=>ids[id], querySelector:()=>({content:''}), querySelectorAll:()=>buttons,
    addEventListener:(type,fn)=>{documentEvents[type]=fn;}};
  const location = {hash:options.hash ?? '#token=test-token', pathname:'/', origin:'http://pc:8765', href:'http://pc:8765/'};
  const context = vm.createContext({document, window:{addEventListener:(type,fn)=>{events[type]=fn;}},
    localStorage:storage(local), sessionStorage:storage(session), URLSearchParams, URL, AbortController,
    performance:{now:()=>clock}, Date:class extends Date {static now(){return wall;}}, location,
    history:{replaceState(_,__,value){location.hash=value.includes('#')?value.slice(value.indexOf('#')):'';}},
    setTimeout:(fn,ms)=>schedule(fn,ms),clearTimeout:id=>timers.delete(id),
    setInterval:(fn,ms)=>schedule(fn,ms,ms),clearInterval:id=>timers.delete(id),
    fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject,done:false}))});
  try {vm.runInContext(script,context);} catch(error){errors.push(error);}
  async function advance(ms) {
    const end = clock+ms;
    for (let i=0;i<2000;i++) {
      const found=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
      if(!found) break;
      const [id,t]=found;wall+=t.at-clock;clock=t.at;timers.delete(id);
      if(t.interval) timers.set(id,{...t,at:clock+t.interval});
      try {const result=t.fn();if(result?.catch) result.catch(e=>errors.push(e));} catch(e){errors.push(e);}
      await flush();
    }
    wall+=end-clock;clock=end;await flush();
  }
  function pending(url) {return requests.find(r=>!r.done && !r.options.signal.aborted && r.url===url);}
  async function respond(request,value,status=200) {
    assert.ok(request,'Expected an outstanding request');request.done=true;
    request.resolve({ok:status>=200&&status<300,status,json:async()=>value});await flush();
  }
  async function initial() {await respond(pending('/api/status'),state());}
  return {ids,buttons,requests,errors,events,documentEvents,document,context,advance,pending,respond,initial,local,session,location,
    button:action=>buttons.find(b=>b.dataset.action===action),setClock:value=>{clock=value;},setWallClock:value=>{wall=value;}};
}

async function oldBusyResponseAfterSuccess() {
  const h=harness();await h.initial();await h.advance(800);
  const oldPoll=h.pending('/api/status');assert.ok(oldPoll);
  const click=h.button('pause').listeners.click();
  await h.respond(h.pending('/api/control'),{ok:true,message:'暂停成功',state:state(3,true)});await click;
  await h.respond(oldPoll,state(2,false,{busy:true}));
  assert.equal(h.button('resume').disabled,false,'Old busy status must not disable a completed pause');
  assert.equal(h.button('stop').disabled,false);
}

async function oldNetworkFailureAfterSuccess() {
  const h=harness();await h.initial();await h.advance(800);
  const oldPoll=h.pending('/api/status');
  const click=h.button('pause').listeners.click();
  await h.respond(h.pending('/api/control'),{ok:true,message:'暂停成功',state:state(3,true)});await click;
  oldPoll.done=true;oldPoll.reject(Error('late network failure'));await flush();
  assert.equal(h.button('resume').disabled,false,'A superseded poll failure must not overwrite successful control');
}

async function soundToggleVersusPolling() {
  const h=harness();await h.initial();await h.advance(800);
  const oldPoll=h.pending('/api/status');
  h.ids.sound.checked=false;const change=h.ids.sound.listeners.change();
  await h.respond(oldPoll,state(2));
  assert.equal(h.ids.sound.checked,false,'Polling must not undo an in-flight sound toggle');
  assert.equal(h.ids.sound.disabled,true);
  await h.respond(h.pending('/api/sound'),{ok:true,state:state(3,false,{sound_enabled:false})});await change;
  assert.equal(h.ids.sound.disabled,false);
}

async function repeatedPauseResumeWithReorderedResponses() {
  const h=harness();await h.initial();let revision=1;
  for(let i=0;i<120;i++) {
    await h.advance(800);const oldPoll=h.pending('/api/status');
    const action=i%2===0?'pause':'resume';
    assert.equal(h.button(action).disabled,false,`Correct action enabled before step ${i}`);
    const click=h.button(action).listeners.click();
    await h.respond(h.pending('/api/control'),{ok:true,message:'成功',state:state(revision+2,action==='pause')});
    await click;
    if(oldPoll) await h.respond(oldPoll,state(revision+1,action!=='pause',{busy:true}));
    revision+=2;
    assert.equal(h.button(action==='pause'?'resume':'pause').disabled,false,`No grey lock at step ${i}`);
    assert.equal(h.button('stop').disabled,false);
  }
  assert.equal(h.requests.filter(r=>r.url==='/api/control').length,120,'No automatic retry or duplicate controls');
  assert.deepEqual(h.errors,[]);
}

async function blockedStorageStillLoadsAndControls() {
  const h=harness(true);assert.deepEqual(h.errors,[],'Private/storage-blocked browsers must still initialize');
  await h.initial();assert.equal(h.button('pause').disabled,false);
}

async function staleWhileBackgroundedAndPageResume() {
  const h=harness();await h.initial();
  h.setClock(10000);
  h.events.pageshow?.();h.documentEvents.visibilitychange?.();await flush();
  assert.equal(h.button('pause').disabled,true,'Expired page state must not remain actionable on return');
  const request=h.requests.filter(r=>!r.done&&r.url==='/api/status').at(-1);
  await h.respond(request,state(20,true));
  assert.equal(h.button('resume').disabled,false,'Returning to the page refreshes status and unlocks controls');
}

async function rejectedControlRefreshesStateWithoutRetry() {
  const h=harness();await h.initial();
  const click=h.button('pause').listeners.click();
  await h.respond(h.pending('/api/control'),{message:'OBS refused'},409);await click;
  await h.respond(h.pending('/api/status'),state(3));
  assert.equal(h.button('pause').disabled,false);
  assert.equal(h.requests.filter(r=>r.url==='/api/control').length,1);
}

async function unknownControlResultReconcilesWithoutReplay() {
  const h=harness();await h.initial();
  const click=h.button('pause').listeners.click();
  const command=h.pending('/api/control');command.done=true;
  command.reject(Object.assign(Error('timeout'),{name:'AbortError'}));await click;
  await h.respond(h.pending('/api/status'),state(5,true));
  assert.equal(h.button('resume').disabled,false);
  assert.equal(h.requests.filter(r=>r.url==='/api/control').length,1);
}

async function newerPollWinsOverLateControlResponse() {
  const h=harness();await h.initial();
  const click=h.button('pause').listeners.click();
  h.events.pageshow();await flush();
  await h.respond(h.pending('/api/status'),state(10,false));
  await h.respond(h.pending('/api/control'),{ok:true,message:'成功',state:state(9,true)});await click;
  assert.equal(h.button('pause').disabled,false);
  assert.equal(h.button('resume').disabled,true);
}

async function duplicateTapDoesNotSendDuplicateCommand() {
  const h=harness();await h.initial();
  const click=h.button('pause').listeners.click();
  await h.button('pause').listeners.click();
  assert.equal(h.requests.filter(r=>r.url==='/api/control').length,1);
  await h.respond(h.pending('/api/control'),{ok:true,message:'成功',state:state(3,true)});await click;
  assert.equal(h.button('resume').disabled,false);
}

async function failedSoundSaveRestoresServerState() {
  const h=harness();await h.initial();
  h.ids.sound.checked=false;const change=h.ids.sound.listeners.change();
  await h.respond(h.pending('/api/sound'),{message:'save failed'},500);await change;
  assert.equal(h.ids.sound.checked,true);
  assert.equal(h.ids.sound.disabled,false);
}

async function timedOutPollRecoversAndMatchesVisualAvailability() {
  const h=harness();await h.initial();await h.advance(5000);
  const request=h.requests.find(r=>!r.done && r.url==='/api/status');request.done=true;
  request.reject(Object.assign(Error('timeout'),{name:'AbortError'}));await flush();
  assert.equal(h.button('pause').disabled,true);
  assert.ok(h.ids.notice.textContent.includes('连接超时'));
  await h.advance(800);await h.respond(h.pending('/api/status'),state(20,true));
  for(const button of h.buttons) {
    assert.equal(button.dataset.available,String(!button.disabled));
    assert.equal(button.attributes['aria-disabled'],String(button.disabled));
  }
  assert.equal(h.button('resume').disabled,false);
}

async function slowControlUsesTheDesktopConfirmationBudget() {
  const h = harness();
  await h.respond(h.pending('/api/status'), state(1, false, {control_timeout_ms:22000}));
  const click = h.button('pause').listeners.click();
  const request = h.pending('/api/control');
  await h.advance(11000);
  assert.equal(request.options.signal.aborted, false, 'A valid long confirmation must not be aborted at 10 seconds');
  await h.respond(request, {ok:true,message:'Paused',state:state(5, true)}); await click;
  assert.equal(h.button('resume').disabled, false);
  assert.equal(h.requests.filter(r=>r.url==='/api/control').length, 1);
}

async function pendingControlKeepsRecordingMetricsLive() {
  const h=harness();await h.initial();
  const click=h.button('pause').listeners.click();
  const command=h.pending('/api/control');
  await h.respond(h.pending('/api/status'),state(2,false,{busy:true,bytes:2097152,size_text:'2.00 MB',size_exact_text:'2,097,152 字节'}));
  assert.equal(h.ids.size.textContent,'2.00 MB');
  assert.ok(h.buttons.every(b=>b.disabled),'Pending control still blocks duplicate actions');
  await h.respond(command,{ok:true,message:'Paused',state:state(3,true)});await click;
  assert.equal(h.button('resume').disabled,false);
}

async function controlTimeoutAdaptsToRefreshedConfirmationBudget() {
  const h=harness();
  await h.respond(h.pending('/api/status'),state(1,false,{control_timeout_ms:10000}));
  const click=h.button('pause').listeners.click();
  const command=h.pending('/api/control');
  await h.respond(h.pending('/api/status'),state(2,false,{busy:true,control_timeout_ms:40000}));
  await h.advance(800);
  const poll=h.pending('/api/status');poll.done=true;poll.reject(Error('temporary Wi-Fi loss'));await flush();
  await h.advance(15000);
  assert.equal(command.options.signal.aborted,false,'A refreshed frame-rate budget extends this in-flight request');
  await h.respond(command,{ok:true,message:'Paused',state:state(3,true)});await click;
  assert.equal(h.button('resume').disabled,false);
  assert.equal(h.requests.filter(r=>r.url==='/api/control').length,1);
}

module.exports = {harness, state, flush};
if (require.main === module) (async()=>{
  let failed=0;
  for(const test of [oldBusyResponseAfterSuccess,oldNetworkFailureAfterSuccess,soundToggleVersusPolling,
    repeatedPauseResumeWithReorderedResponses,blockedStorageStillLoadsAndControls,staleWhileBackgroundedAndPageResume,
    rejectedControlRefreshesStateWithoutRetry,unknownControlResultReconcilesWithoutReplay,newerPollWinsOverLateControlResponse,
    duplicateTapDoesNotSendDuplicateCommand,failedSoundSaveRestoresServerState,timedOutPollRecoversAndMatchesVisualAvailability,
    slowControlUsesTheDesktopConfirmationBudget,pendingControlKeepsRecordingMetricsLive,
    controlTimeoutAdaptsToRefreshedConfirmationBudget]) {
    try {await test();console.log('PASS',test.name);} catch(error){failed++;console.error('FAIL',test.name,error.message);}
  }
  if(failed) process.exitCode=1;
})().catch(error=>{console.error(error);process.exitCode=1;});
