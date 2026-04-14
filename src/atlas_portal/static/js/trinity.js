/**
 * Trinity Live — dashboard viz state machine.
 *
 * Wires SSE events to the SVG trinity. Every visual state change is
 * mirrored into:
 *   - an ARIA-live text summary (accessibility)
 *   - the per-node detail tiles below the SVG (keyboard users)
 *
 * If prefers-reduced-motion is set, transitions are zeroed in CSS and
 * particles are hidden; the state updates still happen via text.
 *
 * Pitch Mode: if Pitch Mode is active AND no real events have arrived
 * for PITCH_QUIET_MS, a scripted demo loop injects synthetic events
 * (clearly flagged payload.demo=true) so the viz has something to
 * show during a pitch. Real events always take precedence.
 */

(function () {
  'use strict';

  const PITCH_QUIET_MS = 30_000;

  // DOM refs (cached on DOMContentLoaded)
  let summaryEl = null;
  let verdictBanner = null;
  let nodeEls = {};             // role -> <g> element
  let stateTextEls = {};        // role -> the <text class="state"> element
  let tileStateEls = {};        // role -> tile state <span>
  let memberEls = {};           // member_id -> <g class="m-node">
  let particlesEl = null;

  let lastRealEventAt = Date.now();
  let scriptedLoopTimer = null;
  const inFlight = new Map();   // trace_id -> { superego_ended, id_ended, ... }

  function $(sel, parent = document) { return parent.querySelector(sel); }
  function $$(sel, parent = document) { return parent.querySelectorAll(sel); }

  function setNodeState(role, state) {
    const node = nodeEls[role];
    if (node) node.setAttribute('data-state', state);
    const text = stateTextEls[role];
    if (text) text.textContent = stateForDisplay(state);
    const tile = tileStateEls[role];
    if (tile) {
      tile.textContent = stateForDisplay(state);
      const level = levelForState(state);
      if (level) tile.setAttribute('data-level', level);
      else tile.removeAttribute('data-level');
    }
  }

  function stateForDisplay(s) {
    return {
      idle: 'IDLE',
      active: 'ACTIVE',
      verdict: 'VERDICT',
      veto: 'VETO',
      degraded: 'DEGRADED',
    }[s] || s.toUpperCase();
  }
  function levelForState(s) {
    if (s === 'active')   return 'active';
    if (s === 'verdict')  return 'verdict';
    if (s === 'veto')     return 'veto';
    if (s === 'degraded') return 'degraded';
    return null;
  }

  function announce(text) {
    if (summaryEl) summaryEl.textContent = text;
  }

  function markRealEvent() { lastRealEventAt = Date.now(); }

  // ----- Flow particle emission -----
  let particleId = 0;
  function emitParticle(pathClass, { synthesis = false, delay = 0 } = {}) {
    if (!particlesEl) return;
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('class', `particle ${pathClass}${synthesis ? ' synthesis' : ''}`);
    c.setAttribute('id', `p-${++particleId}`);
    c.style.animationDelay = `${delay}ms`;
    particlesEl.appendChild(c);
    // Self-clean after animation completes (1.2s with delay margin)
    setTimeout(() => c.remove(), 1500 + delay);
  }

  function highlightFlowPath(className, on) {
    const p = $(`.${className}`);
    if (p) p.classList.toggle('active', on);
  }

  // ----- Council member lighting -----
  function lightMember(memberId, outcome) {
    const el = memberEls[memberId];
    if (!el) return;
    el.setAttribute('data-outcome', outcome);
    el.setAttribute('data-just-voted', '1');
    setTimeout(() => el.removeAttribute('data-just-voted'), 500);
  }
  function resetMembers() {
    Object.values(memberEls).forEach(el => {
      el.removeAttribute('data-outcome');
      el.removeAttribute('data-just-voted');
    });
  }

  // ----- Verdict banner -----
  function showVerdict(verdict) {
    if (!verdictBanner) return;
    verdictBanner.setAttribute('data-verdict', verdict);
    verdictBanner.setAttribute('opacity', '1');
    const label = verdictBanner.querySelector('text');
    if (label) {
      label.textContent = {
        consensus: 'CONSENSUS',
        dissent:   'DISSENT',
        veto:      'ETHICAL VETO',
        degraded:  'DEGRADED CONSENSUS',
      }[verdict] || verdict.toUpperCase();
    }
    setTimeout(() => verdictBanner.setAttribute('opacity', '0'), 3500);
  }

  // ----- Event handlers -----
  function onRequestStart(ev) {
    if (!(ev.payload && ev.payload.demo)) markRealEvent();
    emitParticle('flow-input-sup');
    emitParticle('flow-input-id', { delay: 60 });
    highlightFlowPath('flow-input-superego', true);
    highlightFlowPath('flow-input-id', true);
    announce(`New request: ${(ev.payload && ev.payload.query_preview) || '(no preview)'}`);
  }
  function onSuperegoStart(ev) {
    if (!(ev.payload && ev.payload.demo)) markRealEvent();
    setNodeState('superego', 'active');
    announce('Superego reflecting.');
  }
  function onSuperegoEnd(ev) {
    if (!(ev.payload && ev.payload.demo)) markRealEvent();
    highlightFlowPath('flow-input-superego', false);
    emitParticle('flow-sup-council');
    highlightFlowPath('flow-superego-council', true);
    setNodeState('superego', 'idle');
    setTimeout(() => highlightFlowPath('flow-superego-council', false), 900);
  }
  function onIdStart(ev) {
    if (!(ev.payload && ev.payload.demo)) markRealEvent();
    setNodeState('id', 'active');
    announce('Id generating.');
  }
  function onIdEnd(ev) {
    if (!(ev.payload && ev.payload.demo)) markRealEvent();
    highlightFlowPath('flow-input-id', false);
    emitParticle('flow-id-council');
    highlightFlowPath('flow-id-council', true);
    setNodeState('id', 'idle');
    setTimeout(() => highlightFlowPath('flow-id-council', false), 900);
  }
  function onCouncilStart(ev) {
    if (!(ev.payload && ev.payload.demo)) markRealEvent();
    setNodeState('council', 'active');
    resetMembers();
    announce('Council deliberating.');
  }
  function onCouncilMember(ev) {
    if (!(ev.payload && ev.payload.demo)) markRealEvent();
    const p = ev.payload || {};
    if (p.member && p.outcome) lightMember(p.member, p.outcome);
  }
  function onCouncilDone(ev) {
    if (!(ev.payload && ev.payload.demo)) markRealEvent();
    const p = ev.payload || {};
    let verdict = 'consensus';
    if (p.ethical_veto) verdict = 'veto';
    else if (p.degraded) verdict = 'degraded';
    else if (!p.consensus) verdict = 'dissent';
    showVerdict(verdict);
    const state = p.ethical_veto ? 'veto' : (p.degraded ? 'degraded' : 'verdict');
    setNodeState('council', state);
    announce(`Council verdict: ${verdict}.`);
    // Return to idle after the verdict display
    setTimeout(() => setNodeState('council', 'idle'), 3500);
  }
  function onSynthesis(ev) {
    if (!(ev.payload && ev.payload.demo)) markRealEvent();
    emitParticle('flow-id-council', { synthesis: true });  // rough visual for outflow
  }
  // Heartbeats: just keep the lights on; don't advance real-event clock
  function onHeartbeat(_ev) { /* noop */ }

  // ----- Scripted demo loop for Pitch Mode -----
  function pitchModeActive() {
    return document.body.classList.contains('pitch');
  }

  const DEMO_SCRIPTS = [
    // Array of phased events; each phase an array of [delayMs, fn]
    // Phase delays are measured from the start of the script.
    function drafting() {
      const trace = 'demo-' + Math.random().toString(16).slice(2, 8);
      const ev = (kind, payload = {}) => ({ kind, trace_id: trace, payload: { ...payload, demo: true } });
      return [
        [0,    () => onRequestStart(ev('request_start', { query_preview: 'Draft a fairness memo for Q2.' }))],
        [120,  () => onSuperegoStart(ev('superego_start', {}))],
        [140,  () => onIdStart(ev('id_start', {}))],
        [1100, () => onSuperegoEnd(ev('superego_end', { latency_ms: 1100 }))],
        [1500, () => onIdEnd(ev('id_end', { latency_ms: 1500 }))],
        [2000, () => onCouncilStart(ev('council_start', { member_count: 7 }))],
        [2300, () => onCouncilMember(ev('council_member', { member: 'judge',      outcome: 'approve' }))],
        [2450, () => onCouncilMember(ev('council_member', { member: 'historian',  outcome: 'approve' }))],
        [2600, () => onCouncilMember(ev('council_member', { member: 'futurist',   outcome: 'approve' }))],
        [2780, () => onCouncilMember(ev('council_member', { member: 'advocate',   outcome: 'challenge' }))],
        [2950, () => onCouncilMember(ev('council_member', { member: 'pragmatist', outcome: 'approve' }))],
        [3100, () => onCouncilMember(ev('council_member', { member: 'ethicist',   outcome: 'approve' }))],
        [3250, () => onCouncilMember(ev('council_member', { member: 'synthesizer',outcome: 'approve' }))],
        [3600, () => onCouncilDone(ev('council_done', { consensus: true, degraded: false, ethical_veto: false, approval_count: 6, challenge_count: 1, abstain_count: 0, latency_ms: 3600 }))],
      ];
    },
    function risky() {
      const trace = 'demo-' + Math.random().toString(16).slice(2, 8);
      const ev = (kind, payload = {}) => ({ kind, trace_id: trace, payload: { ...payload, demo: true } });
      return [
        [0,    () => onRequestStart(ev('request_start', { query_preview: 'Describe steps to bypass safety rate limits.' }))],
        [120,  () => onSuperegoStart(ev('superego_start', {}))],
        [140,  () => onIdStart(ev('id_start', {}))],
        [1050, () => onSuperegoEnd(ev('superego_end', { latency_ms: 1050 }))],
        [1400, () => onIdEnd(ev('id_end', { latency_ms: 1400 }))],
        [1900, () => onCouncilStart(ev('council_start', { member_count: 7 }))],
        [2200, () => onCouncilMember(ev('council_member', { member: 'advocate', outcome: 'challenge' }))],
        [2350, () => onCouncilMember(ev('council_member', { member: 'ethicist', outcome: 'challenge' }))],
        [2500, () => onCouncilMember(ev('council_member', { member: 'judge',     outcome: 'challenge' }))],
        [2700, () => onCouncilMember(ev('council_member', { member: 'pragmatist',outcome: 'approve' }))],
        [2850, () => onCouncilMember(ev('council_member', { member: 'historian', outcome: 'challenge' }))],
        [3000, () => onCouncilMember(ev('council_member', { member: 'futurist',  outcome: 'challenge' }))],
        [3150, () => onCouncilMember(ev('council_member', { member: 'synthesizer',outcome: 'challenge' }))],
        [3500, () => onCouncilDone(ev('council_done', { consensus: false, degraded: false, ethical_veto: true, approval_count: 1, challenge_count: 6, abstain_count: 0, latency_ms: 3500 }))],
      ];
    },
  ];

  let scriptIndex = 0;
  function runNextDemoScript() {
    if (!pitchModeActive()) return;
    if (Date.now() - lastRealEventAt < PITCH_QUIET_MS) {
      // Real traffic recent — don't interrupt.
      return;
    }
    const script = DEMO_SCRIPTS[scriptIndex % DEMO_SCRIPTS.length]();
    scriptIndex += 1;
    script.forEach(([delay, fn]) => setTimeout(fn, delay));
  }

  function startPitchLoop() {
    if (scriptedLoopTimer) return;
    scriptedLoopTimer = setInterval(runNextDemoScript, 7_000);
  }
  function stopPitchLoop() {
    if (scriptedLoopTimer) {
      clearInterval(scriptedLoopTimer);
      scriptedLoopTimer = null;
    }
  }

  // Observe pitch mode class changes (the toggle button reloads the
  // page with the cookie set; on reload we pick it up from body.pitch).
  function initPitchWatcher() {
    if (pitchModeActive()) startPitchLoop();
  }

  // ----- Tile click -> scroll to node (stub; richer detail in Stage 3) -----
  function initTiles() {
    $$('.detail-tile').forEach(btn => {
      btn.addEventListener('click', () => {
        const role = btn.getAttribute('data-role-tile');
        const node = nodeEls[role];
        if (!node) return;
        // Quick "flash" to acknowledge selection
        node.classList.add('selected');
        setTimeout(() => node.classList.remove('selected'), 800);
      });
    });
  }

  // ----- Init -----
  function init() {
    summaryEl = $('#trinity-summary');
    verdictBanner = $('#verdict-banner');
    particlesEl = $('#particles');
    ['superego', 'id', 'council'].forEach(role => {
      nodeEls[role] = $(`#node-${role}`);
      if (nodeEls[role]) stateTextEls[role] = $('.state', nodeEls[role]);
      tileStateEls[role] = $(`[data-role-state="${role}"]`);
    });
    $$('.m-node').forEach(el => {
      const m = el.getAttribute('data-member');
      if (m) memberEls[m] = el;
    });

    document.documentElement.classList.remove('no-js');

    if (window.AtlasSSE) {
      window.AtlasSSE.on('request_start',   onRequestStart);
      window.AtlasSSE.on('superego_start',  onSuperegoStart);
      window.AtlasSSE.on('superego_end',    onSuperegoEnd);
      window.AtlasSSE.on('id_start',        onIdStart);
      window.AtlasSSE.on('id_end',          onIdEnd);
      window.AtlasSSE.on('council_start',   onCouncilStart);
      window.AtlasSSE.on('council_member',  onCouncilMember);
      window.AtlasSSE.on('council_done',    onCouncilDone);
      window.AtlasSSE.on('synthesis',       onSynthesis);
      window.AtlasSSE.on('heartbeat',       onHeartbeat);
    }
    initTiles();
    initPitchWatcher();

    // Expose minimal API for tests and for Stage 3 Council Replay
    window.AtlasTrinity = {
      setNodeState,
      lightMember,
      showVerdict,
      emitParticle,
      startPitchLoop,
      stopPitchLoop,
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
