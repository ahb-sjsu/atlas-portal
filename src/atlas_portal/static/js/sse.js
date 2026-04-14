/**
 * Atlas Portal SSE client.
 *
 * Exposes a minimal event bus on window.AtlasSSE:
 *
 *   AtlasSSE.on(kind, handler)   // subscribe; returns an unsubscribe fn
 *   AtlasSSE.state               // 'connecting' | 'connected' | 'disconnected'
 *
 * Connects to /api/events/trinity and dispatches parsed events to
 * subscribers keyed on kind (e.g. "request_start", "council_done").
 * Reconnects with capped exponential backoff. Respects Last-Event-ID
 * so missed events are delivered on reconnect.
 */

(function () {
  'use strict';

  const url = '/api/events/trinity';
  const handlers = new Map();  // kind -> Set<handler>
  let source = null;
  let lastEventId = 0;
  let backoffMs = 1000;

  const statusEl = document.getElementById('sse-status');

  function setState(state, label) {
    window.AtlasSSE.state = state;
    if (!statusEl) return;
    statusEl.setAttribute('data-state', state);
    const labelEl = statusEl.querySelector('.label');
    if (labelEl && label) labelEl.textContent = label;
  }

  function dispatch(kind, data) {
    const set = handlers.get(kind);
    if (!set) return;
    set.forEach(h => {
      try { h(data); } catch (e) { console.error('AtlasSSE handler error', e); }
    });
  }

  function connect() {
    setState('connecting', 'connecting…');
    try {
      source = new EventSource(url);
    } catch (e) {
      console.error('EventSource failed', e);
      scheduleReconnect();
      return;
    }

    source.onopen = () => {
      setState('connected', 'live');
      backoffMs = 1000;  // reset on successful open
    };

    source.onerror = () => {
      setState('disconnected', 'offline');
      try { source.close(); } catch (_e) { /* noop */ }
      scheduleReconnect();
    };

    // Register listener for each known event kind. EventSource delivers
    // typed events only to kind-specific listeners (NOT onmessage), so
    // we register them explicitly.
    const kinds = [
      'request_start', 'request_end',
      'superego_start', 'superego_end',
      'id_start', 'id_end',
      'council_start', 'council_member', 'council_done',
      'synthesis', 'backend_health', 'fallback_activated', 'heartbeat',
    ];
    kinds.forEach(kind => {
      source.addEventListener(kind, (ev) => {
        if (ev.lastEventId) {
          const n = parseInt(ev.lastEventId, 10);
          if (!isNaN(n) && n > lastEventId) lastEventId = n;
        }
        let payload = {};
        try { payload = JSON.parse(ev.data); } catch (_e) { /* noop */ }
        dispatch(kind, { ...payload, kind });
      });
    });
  }

  function scheduleReconnect() {
    const delay = Math.min(backoffMs, 30_000);
    backoffMs = Math.min(backoffMs * 2, 30_000);
    setTimeout(connect, delay);
  }

  window.AtlasSSE = {
    state: 'disconnected',
    on(kind, handler) {
      if (!handlers.has(kind)) handlers.set(kind, new Set());
      handlers.get(kind).add(handler);
      return () => handlers.get(kind).delete(handler);
    },
  };

  // Pause while tab is hidden to save CPU; reopen on return.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (source) { try { source.close(); } catch (_e) { /* noop */ } }
      setState('disconnected', 'paused');
    } else if (window.AtlasSSE.state !== 'connected') {
      connect();
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', connect);
  } else {
    connect();
  }
})();
