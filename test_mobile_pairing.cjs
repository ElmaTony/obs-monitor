const assert = require('node:assert/strict');
const vm = require('node:vm');
const {harness, state, flush} = require('./test_mobile_races.cjs');
const savedToken = 'A'.repeat(43);

async function closeAndReopenBrowserAtRoot() {
  const local = new Map();
  const first = harness(false, {local, hash:'#token='+savedToken});
  await first.initial();
  assert.equal(local.get('obs-token'), savedToken);
  const second = harness(false, {local, session:new Map(), hash:''});
  const request = second.pending('/api/status');
  assert.equal(request.options.headers['X-OBS-Token'], savedToken);
  await second.respond(request, state());
  assert.equal(second.button('pause').disabled, false);
  assert.equal(second.ids.pairing.hidden, true);
}

async function cookieOnlyPairingWorksWithBlockedStorage() {
  const h = harness(true, {hash:''});
  const request = h.pending('/api/status');
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(request.options.headers['X-OBS-Token'], undefined);
  await h.respond(request, state());
  assert.equal(h.button('pause').disabled, false);
  assert.equal(h.ids.pairing.hidden, true);
}

async function legacySessionPairingMigratesToPersistentStorage() {
  const local = new Map(), session = new Map([['obs-token', savedToken]]);
  const h = harness(false, {local, session, hash:''});
  assert.equal(local.get('obs-token'), savedToken);
  await h.initial();
  assert.equal(h.button('pause').disabled, false);
}

async function originalFullLinkSurvivesBlockedStorage() {
  const h = harness(true, {hash:'#token='+savedToken});
  await h.initial();
  assert.equal(h.location.hash, '#token='+savedToken);
}

async function invalidPairingOffersLinkInsteadOfReloadLoop() {
  const h = harness(false, {hash:'#token='+savedToken});
  await h.respond(h.pending('/api/status'), {message:'请重新连接此设备'}, 401);
  assert.equal(h.ids.pairing.hidden, false);
  assert.equal(h.local.has('obs-token'), false);
  assert.equal(h.session.has('obs-token'), false);
  await h.advance(30000);
  assert.equal(h.requests.length, 1);
  h.ids.pairLink.value = 'http://pc:8765/#token='+savedToken;
  h.ids.pairConnect.listeners.click();
  const request = h.pending('/api/status');
  assert.equal(request.options.headers['X-OBS-Token'], savedToken);
  await h.respond(request, state());
  assert.equal(h.button('pause').disabled, false);
  assert.equal(h.ids.pairing.hidden, true);
}

async function foreignPairingLinkIsNotSentToCurrentComputer() {
  const h = harness(false, {hash:''});
  await h.respond(h.pending('/api/status'), {message:'尚未配对'}, 401);
  h.ids.pairLink.value = 'http://other-pc:8765/#token='+savedToken;
  h.ids.pairConnect.listeners.click();
  assert.equal(h.requests.length, 1);
  assert.ok(h.ids.notice.textContent.includes('不同'));
}

async function forgetClearsPairingOnlyAfterServerAcknowledges() {
  const h = harness(false, {hash:'#token='+savedToken});
  await h.initial();
  const forget = h.ids.forget.listeners.click();
  assert.equal(h.local.get('obs-token'), savedToken);
  await h.respond(h.pending('/api/unpair'), {ok:true});
  await forget;
  assert.equal(h.local.has('obs-token'), false);
  assert.equal(h.session.has('obs-token'), false);
  assert.equal(h.location.hash, '');
  assert.equal(h.ids.pairing.hidden, false);
  assert.ok(h.buttons.every(button=>button.disabled));
}

async function failedForgetKeepsExistingPairing() {
  const h = harness(false, {hash:'#token='+savedToken});
  await h.initial();
  const forget = h.ids.forget.listeners.click();
  await h.respond(h.pending('/api/unpair'), {message:'temporary failure'}, 500);
  await forget;
  assert.equal(h.local.get('obs-token'), savedToken);
  assert.equal(h.button('pause').disabled, false);
}

async function forgettingDeviceCannotRaceRecordingOrLatePolling() {
  const h = harness(); await h.initial(); await h.advance(800);
  const oldPoll = h.pending('/api/status');
  const forget = h.ids.forget.listeners.click();
  assert.ok(h.buttons.every(button=>button.disabled));
  assert.equal(h.ids.sound.disabled, true);
  await h.button('pause').listeners.click();
  assert.equal(h.requests.filter(request=>request.url==='/api/control').length, 0);
  await h.respond(h.pending('/api/unpair'), {ok:true}); await forget;
  await h.respond(oldPoll, state(30));
  assert.ok(h.buttons.every(button=>button.disabled));
  assert.equal(h.ids.pairing.hidden, false);
}

async function lostConnectionMarksRetainedSizeAsOldData() {
  const h = harness(); await h.initial(); await h.advance(800);
  const request = h.pending('/api/status'); request.done = true;
  request.reject(Error('offline')); await flush();
  assert.ok(h.ids.exactSize.textContent.endsWith(' · 上次有效数据'));
  assert.ok(h.ids.sizeHint.textContent.includes('上次有效数据'));
  assert.ok(h.buttons.every(button=>button.disabled));
}

async function restartedDesktopAcceptsLowerRevisionAndIgnoresOldInstance() {
  const h = harness();
  await h.respond(h.pending('/api/status'), state(100, true));
  vm.runInContext('render('+JSON.stringify(state(1, false, {instance_id:'new-server'}))+')', h.context);
  assert.equal(h.button('pause').disabled, false);
  vm.runInContext('render('+JSON.stringify(state(101, true))+')', h.context);
  assert.equal(h.button('pause').disabled, false);
  assert.equal(h.button('resume').disabled, true);
}

async function hiddenPageStopsPollingAndImmediatelyResumes() {
  const h = harness(); await h.initial();
  h.document.hidden = true;
  h.documentEvents.visibilitychange();
  const before = h.requests.length;
  await h.advance(60000);
  assert.equal(h.requests.length, before);
  h.document.hidden = false;
  h.documentEvents.visibilitychange();
  assert.equal(h.button('pause').disabled, true);
  await h.respond(h.pending('/api/status'), state(10, true));
  assert.equal(h.button('resume').disabled, false);
}

async function deviceSleepExpiresStateEvenWhenMonotonicClockStops() {
  const h = harness(); await h.initial();
  h.setWallClock(160000);
  h.events.pageshow();
  assert.equal(h.button('pause').disabled, true);
  await h.respond(h.pending('/api/status'), state(10));
  assert.equal(h.button('pause').disabled, false);
}

async function networkBackoffAndManualReconnectRecoverPairing() {
  const h = harness(); await h.initial();
  for (let i=0; i<4; i++) {
    await h.advance(i<2 ? 800 : 800 * 2 ** (i-1));
    const request = h.pending('/api/status');
    assert.ok(request); request.done = true; request.reject(Error('Wi-Fi disconnected')); await flush();
  }
  assert.equal(h.ids.pairing.hidden, true);
  assert.equal(h.ids.retry.hidden, false);
  const before = h.requests.length;
  await h.advance(800);
  assert.equal(h.requests.length, before);
  h.ids.retry.listeners.click();
  await h.respond(h.pending('/api/status'), state(30));
  assert.equal(h.button('pause').disabled, false);
}

async function oldUnauthorizedSoundResponseCannotEraseNewPairing() {
  const h = harness(false, {hash:'#token='+savedToken}); await h.initial();
  h.ids.sound.checked = false;
  const change = h.ids.sound.listeners.change();
  const oldSound = h.pending('/api/sound');
  await h.advance(800);
  await h.respond(h.pending('/api/status'), {message:'Pairing was reset'}, 401);
  const replacement = 'B'.repeat(43);
  h.ids.pairLink.value = 'http://pc:8765/#token='+replacement;
  h.ids.pairConnect.listeners.click();
  await h.respond(h.pending('/api/status'), state(20));
  await h.respond(oldSound, {message:'Old token is no longer valid'}, 401); await change;
  assert.equal(h.ids.pairing.hidden, true);
  assert.equal(h.local.get('obs-token'), replacement);
  assert.equal(h.button('pause').disabled, false);
}

async function oldControlResponseCannotUnpairAfterDesktopResetAndReconnection() {
  const h = harness(false, {hash:'#token='+savedToken}); await h.initial();
  const click = h.button('pause').listeners.click();
  const oldControl = h.pending('/api/control');
  h.events.pageshow();
  await h.respond(h.pending('/api/status'), {message:'Pairing was reset'}, 401);
  const replacement = 'C'.repeat(43);
  h.ids.pairLink.value = 'http://pc:8765/#token='+replacement;
  h.ids.pairConnect.listeners.click();
  await h.respond(h.pending('/api/status'), state(30, true));
  await h.respond(oldControl, {message:'Old token is no longer valid'}, 401); await click;
  assert.equal(h.ids.pairing.hidden, true);
  assert.equal(h.local.get('obs-token'), replacement);
  assert.equal(h.button('resume').disabled, false);
}

async function oldSuccessfulResponseCannotOverwriteStateAfterNewPairing() {
  const h = harness(false, {hash:'#token='+savedToken}); await h.initial();
  h.ids.sound.checked = false;
  const change = h.ids.sound.listeners.change();
  const oldSound = h.pending('/api/sound');
  await h.advance(800);
  await h.respond(h.pending('/api/status'), {message:'Pairing was reset'}, 401);
  h.ids.pairLink.value = 'http://pc:8765/#token='+'D'.repeat(43);
  h.ids.pairConnect.listeners.click();
  await h.respond(h.pending('/api/status'), state(20, false, {sound_enabled:true}));
  await h.respond(oldSound, {ok:true,state:state(999, true, {sound_enabled:false})}); await change;
  assert.equal(h.ids.sound.checked, true);
  assert.equal(h.button('pause').disabled, false);
}

(async()=>{
  let failed = 0;
  const tests = [closeAndReopenBrowserAtRoot,cookieOnlyPairingWorksWithBlockedStorage,
    legacySessionPairingMigratesToPersistentStorage,originalFullLinkSurvivesBlockedStorage,
    invalidPairingOffersLinkInsteadOfReloadLoop,foreignPairingLinkIsNotSentToCurrentComputer,
    forgetClearsPairingOnlyAfterServerAcknowledges,failedForgetKeepsExistingPairing,
    forgettingDeviceCannotRaceRecordingOrLatePolling,lostConnectionMarksRetainedSizeAsOldData,
    restartedDesktopAcceptsLowerRevisionAndIgnoresOldInstance,hiddenPageStopsPollingAndImmediatelyResumes,
    deviceSleepExpiresStateEvenWhenMonotonicClockStops,networkBackoffAndManualReconnectRecoverPairing,
    oldUnauthorizedSoundResponseCannotEraseNewPairing,oldControlResponseCannotUnpairAfterDesktopResetAndReconnection,
    oldSuccessfulResponseCannotOverwriteStateAfterNewPairing];
  for (const test of tests) {
    try {await test();console.log('PASS',test.name);} catch(error){failed++;console.error('FAIL',test.name,error.message);}
  }
  if (failed) process.exitCode = 1;
})().catch(error=>{console.error(error);process.exitCode=1;});
