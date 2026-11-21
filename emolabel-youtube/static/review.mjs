// Paused time includes hidden tabs, server requests and gaps beyond 90 seconds.
export class ActiveClock {
  constructor(now = () => performance.now()) {
    this.now = now;
    this.accumulated = 0;
    this.since = null;
  }

  elapsed() {
    return this.since === null ? 0 : Math.min(90000, this.now() - this.since);
  }

  resume() {
    if (this.since === null) this.since = this.now();
  }

  activity() {
    if (this.since === null) return;
    this.accumulated += this.elapsed();
    this.since = this.now();
  }

  pause() {
    this.accumulated += this.elapsed();
    this.since = null;
  }

  seconds() {
    return Math.round(this.accumulated + this.elapsed()) / 1000;
  }
}

export function queueRows(rows, filter, blind) {
  if (filter === 'history') return rows.filter(row => row.status !== 'pending');
  const pending = rows.filter(row => row.status === 'pending' &&
    Boolean(row.low_quality) === (filter === 'quality'));
  if (!blind) pending.sort((a, b) => Number(Boolean(b.model_disagreement)) - Number(Boolean(a.model_disagreement)));
  return pending;
}

export function reviewFields(row, blind) {
  const saved = blind ? {} : row.review || {};
  return {
    transcript: saved.transcript ?? (blind ? '' : row.transcript || ''),
    emotion: saved.emotion ?? null,
    gender: saved.gender || 'unknown',
    actor: saved.actor || '',
    notes: saved.notes || '',
    reason: saved.reason || '',
    confidence: saved.confidence ?? null,
    quality: saved.quality ?? null,
    ambiguous: saved.ambiguous ?? null,
    verified: false,
  };
}

const NON_TEXT_INPUT_TYPES = new Set(['checkbox', 'radio', 'button', 'submit', 'range']);

export function isShortcutBlocked(active, code) {
  const tag = active?.tagName;
  const nonTextInput = tag === 'INPUT' && NON_TEXT_INPUT_TYPES.has((active.type || '').toLowerCase());
  if (tag === 'TEXTAREA' || tag === 'SELECT' || (tag === 'INPUT' && !nonTextInput)) return true;
  if ((tag === 'BUTTON' || nonTextInput) && code === 'Space') return true;
  return false;
}
const BOUNDARY_EPSILON = 0.02;

function boundaryEpsilon(region) {
  return Math.min(BOUNDARY_EPSILON, Math.max(0, region.end - region.start) / 4);
}

export function playSelection(ws, region, restart = false) {
  const time = ws.getCurrentTime();
  if (region) {
    const eps = boundaryEpsilon(region);
    if (restart || time < region.start - eps || time >= region.end - eps) {
      ws.setTime(region.start);
    }
  }
  return ws.play();
}

export function enforceSelection(ws, region, time, loop) {
  if (!region || !ws.isPlaying()) return;
  const eps = boundaryEpsilon(region);
  if (time >= region.end) {
    if (!loop) ws.pause();
    ws.setTime(loop ? region.start : region.end);
  } else if (time < region.start - eps) {
    ws.setTime(region.start);
  }
}

export function bindPlaybackEvents(ws, state, button) {
  ws.on('play', () => { button.textContent = '⏸ 일시정지'; });
  // A natural media end can emit pause before finish. Keep the mode until finish.
  ws.on('pause', () => { button.textContent = '▶ 재생'; });
  ws.on('finish', () => {
    const repeat = !state.playingOriginal && state.loop && state.region;
    state.playingOriginal = false;
    if (repeat) playSelection(ws, state.region, true);
  });
}
