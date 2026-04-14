/**
 * Council Replay — swim-lane timeline renderer + transport.
 *
 * Fetches the list of recent deliberations, renders the sidebar,
 * and on selection fetches the full deliberation and draws the
 * 7-lane timeline. Play/pause/scrub controls advance a playhead
 * across the X axis; bars before the playhead are filled at full
 * opacity, bars after are dimmed (.pre-playhead).
 *
 * Live updates: a new council_done event on AtlasSSE prepends the
 * new deliberation to the sidebar without reloading.
 */

(function () {
  'use strict';

  // ---- DOM refs ----------
  let listEl, traceIdEl, queryEl, playBtn, speedSel, scrubEl, timeEl;
  let canvasSvg, summaryEl;
  let statVerdict, statApprove, statChallenge, statAbstain, statLatency;

  // ---- Config ----------
  const MEMBERS = ['judge', 'advocate', 'synthesizer', 'ethicist', 'historian', 'futurist', 'pragmatist'];
  const MEMBER_LABELS = {
    judge: 'Judge',
    advocate: 'Advocate',
    synthesizer: 'Synthesizer',
    ethicist: 'Ethicist',
    historian: 'Historian',
    futurist: 'Futurist',
    pragmatist: 'Pragmatist',
  };
  const CANVAS_W = 800;
  const CANVAS_H = 360;
  const LEFT_GUTTER = 120;
  const TOP_GUTTER = 24;
  const BOTTOM_GUTTER = 40;
  const LANE_PAD = 4;

  // ---- State ----------
  let currentDeliberation = null;
  let playing = false;
  let rafId = null;
  let playheadMs = 0;
  let durationMs = 1000;
  let lastTickAt = 0;
  let speed = 1;

  function $(id) { return document.getElementById(id); }

  // ---- Sidebar ----------
  function renderList(items) {
    if (!listEl) return;
    if (!items || items.length === 0) {
      listEl.innerHTML = '<li class="empty">No deliberations yet. Trigger one with Pitch Mode, or wait for real traffic.</li>';
      return;
    }
    listEl.innerHTML = '';
    items.forEach(item => listEl.appendChild(sidebarItem(item)));
  }

  function sidebarItem(item) {
    const li = document.createElement('li');
    li.className = 'replay-item';
    li.setAttribute('role', 'button');
    li.tabIndex = 0;
    li.dataset.traceId = item.trace_id;

    const ts = document.createElement('span');
    ts.className = 'ts';
    const d = new Date(item.completed_ts * 1000);
    ts.textContent = `${d.toLocaleTimeString()} · ${item.trace_id}`;

    const preview = document.createElement('span');
    preview.className = 'preview';
    preview.textContent = item.query_preview || '(no preview)';

    const badges = document.createElement('div');
    badges.className = 'badges';
    const verdict = verdictFor(item);
    const vb = document.createElement('span');
    vb.className = `badge ${verdict}`;
    vb.textContent = verdict;
    badges.appendChild(vb);

    li.append(ts, preview, badges);
    li.addEventListener('click', () => selectDeliberation(item.trace_id));
    li.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        selectDeliberation(item.trace_id);
      }
    });
    return li;
  }

  function verdictFor(item) {
    if (item.ethical_veto) return 'veto';
    if (item.degraded) return 'degraded';
    if (item.consensus) return 'consensus';
    return 'dissent';
  }

  function markActive(traceId) {
    if (!listEl) return;
    Array.from(listEl.querySelectorAll('.replay-item')).forEach(el => {
      el.classList.toggle('active', el.dataset.traceId === traceId);
    });
  }

  // ---- Fetching ----------
  function refreshList() {
    fetch('/api/council/deliberations?limit=50')
      .then(r => r.json())
      .then(data => renderList(data.items || []))
      .catch(() => {});
  }

  function selectDeliberation(traceId) {
    fetch(`/api/council/deliberations/${encodeURIComponent(traceId)}`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (!d) return;
        loadDeliberation(d);
        markActive(traceId);
      })
      .catch(() => {});
  }

  // ---- Rendering ----------
  function loadDeliberation(d) {
    currentDeliberation = d;
    durationMs = Math.max(100, d.total_latency_ms || 1000);
    playheadMs = 0;
    playing = false;

    traceIdEl.textContent = d.trace_id;
    queryEl.textContent = d.query_preview || '(no preview)';
    enableControls(true);
    updatePlayButton();
    updateTimeDisplay();
    scrubEl.value = '0';
    scrubEl.max = String(durationMs);

    drawLanes(d);
    updatePlayhead();
    updateStats(d);

    summaryEl.textContent = `Loaded deliberation ${d.trace_id}. ` +
      `${d.approval_count} approve, ${d.challenge_count} challenge, ${d.abstain_count} abstain. ` +
      `Total ${(durationMs / 1000).toFixed(2)}s.`;
  }

  function ns(tag, attrs = {}, text = null) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
    if (text != null) el.textContent = text;
    return el;
  }

  function drawLanes(d) {
    canvasSvg.innerHTML = '';
    const laneCount = MEMBERS.length;
    const usableH = CANVAS_H - TOP_GUTTER - BOTTOM_GUTTER;
    const laneH = usableH / laneCount;
    const usableW = CANVAS_W - LEFT_GUTTER - 10;

    // Lane backgrounds + labels
    MEMBERS.forEach((m, i) => {
      const y = TOP_GUTTER + i * laneH;
      canvasSvg.appendChild(ns('rect', {
        class: i % 2 === 0 ? 'swim-lane-bg' : 'swim-lane-alt',
        x: LEFT_GUTTER, y: y, width: usableW, height: laneH,
      }));
      canvasSvg.appendChild(ns('text', {
        class: 'swim-lane-label',
        x: LEFT_GUTTER - 10, y: y + laneH / 2 + 4,
        'text-anchor': 'end',
      }, MEMBER_LABELS[m]));
    });

    // Grid ticks (4 vertical lines)
    const tickCount = 5;
    for (let i = 0; i <= tickCount; i++) {
      const x = LEFT_GUTTER + (usableW * i / tickCount);
      canvasSvg.appendChild(ns('line', {
        class: 'swim-grid-line',
        x1: x, y1: TOP_GUTTER,
        x2: x, y2: CANVAS_H - BOTTOM_GUTTER,
      }));
      const tMs = (durationMs * i / tickCount).toFixed(0);
      canvasSvg.appendChild(ns('text', {
        class: 'swim-grid-tick',
        x: x, y: CANVAS_H - BOTTOM_GUTTER + 14, 'text-anchor': 'middle',
      }, `${(parseInt(tMs, 10) / 1000).toFixed(2)}s`));
    }

    // Phase markers (council_start, superego_start, id_start)
    const phases = d.phase_markers || {};
    ['council_start', 'superego_start', 'id_start'].forEach(kind => {
      if (phases[kind] == null) return;
      const t = Math.max(0, Math.min(durationMs, phases[kind]));
      const x = LEFT_GUTTER + (usableW * t / durationMs);
      canvasSvg.appendChild(ns('line', {
        class: `swim-phase-marker phase-${kind}`,
        x1: x, y1: TOP_GUTTER,
        x2: x, y2: CANVAS_H - BOTTOM_GUTTER,
      }));
    });

    // Member bars — one per member event. Width is latency_ms relative to the
    // deliberation's total duration; start position is t_offset_ms — latency.
    (d.members || []).forEach(m => {
      const laneIndex = MEMBERS.indexOf(m.member);
      if (laneIndex < 0) return;
      const y = TOP_GUTTER + laneIndex * laneH + LANE_PAD;
      const h = laneH - LANE_PAD * 2;
      const startT = Math.max(0, m.t_offset_ms - (m.latency_ms || 0));
      const endT = Math.min(durationMs, m.t_offset_ms);
      const x = LEFT_GUTTER + (usableW * startT / durationMs);
      const w = Math.max(3, usableW * (endT - startT) / durationMs);
      canvasSvg.appendChild(ns('rect', {
        class: `swim-member-bar ${m.outcome || 'abstain'} pre-playhead`,
        'data-member': m.member,
        'data-at': m.t_offset_ms,
        x: x, y: y, width: w, height: h, rx: 3,
      }));
    });

    // Playhead line (updated in updatePlayhead)
    const ph = ns('line', {
      class: 'swim-playhead', id: 'swim-playhead',
      x1: LEFT_GUTTER, y1: TOP_GUTTER,
      x2: LEFT_GUTTER, y2: CANVAS_H - BOTTOM_GUTTER,
    });
    canvasSvg.appendChild(ph);
  }

  function updatePlayhead() {
    const ph = $('swim-playhead');
    if (!ph) return;
    const usableW = CANVAS_W - LEFT_GUTTER - 10;
    const frac = durationMs > 0 ? Math.min(1, playheadMs / durationMs) : 0;
    const x = LEFT_GUTTER + usableW * frac;
    ph.setAttribute('x1', x);
    ph.setAttribute('x2', x);

    // Update bar opacity based on whether we've passed them
    canvasSvg.querySelectorAll('.swim-member-bar').forEach(bar => {
      const at = parseFloat(bar.getAttribute('data-at')) || 0;
      bar.classList.toggle('pre-playhead', at > playheadMs);
    });

    // Scrub display
    scrubEl.value = String(playheadMs);
    updateTimeDisplay();
  }

  function updateTimeDisplay() {
    timeEl.textContent = `${(playheadMs / 1000).toFixed(2)} / ${(durationMs / 1000).toFixed(2)}s`;
  }

  // ---- Transport ----------
  function play() {
    if (!currentDeliberation || playing) return;
    if (playheadMs >= durationMs) playheadMs = 0;
    playing = true;
    lastTickAt = performance.now();
    tick();
    updatePlayButton();
  }
  function pause() {
    playing = false;
    if (rafId) { cancelAnimationFrame(rafId); rafId = null; }
    updatePlayButton();
  }
  function toggle() { (playing ? pause : play)(); }

  function tick() {
    rafId = requestAnimationFrame(tick);
    const now = performance.now();
    const dt = now - lastTickAt;
    lastTickAt = now;
    playheadMs += dt * speed;
    if (playheadMs >= durationMs) {
      playheadMs = durationMs;
      pause();
    }
    updatePlayhead();
  }

  function updatePlayButton() {
    if (!playBtn) return;
    playBtn.textContent = playing ? '⏸' : '▶';
    playBtn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
  }

  function enableControls(enabled) {
    [playBtn, speedSel, scrubEl].forEach(el => {
      if (el) el.disabled = !enabled;
    });
  }

  // ---- Stats ----------
  function updateStats(d) {
    const v = verdictFor(d);
    statVerdict.textContent = v.toUpperCase();
    statVerdict.setAttribute('data-verdict', v);
    statApprove.textContent = d.approval_count;
    statChallenge.textContent = d.challenge_count;
    statAbstain.textContent = d.abstain_count;
    statLatency.textContent = `${(d.total_latency_ms / 1000).toFixed(2)}s`;
  }

  // ---- Init ----------
  function init() {
    listEl = $('replay-list');
    traceIdEl = $('replay-trace-id');
    queryEl = $('replay-query');
    playBtn = $('replay-play');
    speedSel = $('replay-speed');
    scrubEl = $('replay-scrub');
    timeEl = $('replay-time');
    canvasSvg = document.querySelector('.swim-svg');
    summaryEl = $('replay-summary');
    statVerdict = $('stat-verdict');
    statApprove = $('stat-approve');
    statChallenge = $('stat-challenge');
    statAbstain = $('stat-abstain');
    statLatency = $('stat-latency');

    if (!listEl || !canvasSvg) return;

    playBtn.addEventListener('click', toggle);
    speedSel.addEventListener('change', e => { speed = parseFloat(e.target.value) || 1; });
    scrubEl.addEventListener('input', e => {
      pause();
      playheadMs = parseFloat(e.target.value) || 0;
      updatePlayhead();
    });
    $('refresh-recent').addEventListener('click', refreshList);

    // Keyboard: space toggles
    document.addEventListener('keydown', e => {
      if (e.key === ' ' && currentDeliberation && document.activeElement?.tagName !== 'INPUT') {
        e.preventDefault();
        toggle();
      }
    });

    // Initial load
    refreshList();

    // Listen for new council_done events and refresh the sidebar.
    if (window.AtlasSSE) {
      window.AtlasSSE.on('council_done', () => {
        // Small delay so the recorder has stored it.
        setTimeout(refreshList, 200);
      });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
