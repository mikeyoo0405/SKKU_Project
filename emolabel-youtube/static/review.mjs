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

export function restoreReviewFields(row, blind, draft) {
  const base = reviewFields(row, blind);
  const valid = Boolean(draft && draft.revision === row.revision && draft.source_version === row.source_version);
  if (!valid) return { fields: base, bounds: null, clock: null, restored: false };
  return {
    fields: { ...base, ...draft.fields },
    bounds: draft.bounds ? { start: draft.bounds.start, end: draft.bounds.end } : null,
    clock: draft.clock || null,
    restored: true,
  };
}

export function canUseEmotionShortcut(state) {
  return Boolean(state.clipId && !state.addition && state.row?.status !== 'superseded');
}

export function canStartAddition(state) {
  return Boolean(!state.busy && !state.addition && state.annotator && state.video && state.ws && state.wsReady &&
    (!state.queue || state.row?.source_url));
}

export function canTranscribeCandidate(state, blind) {
  return Boolean(state.queue && !blind && !state.busy && !state.addition && state.row &&
    state.row.status !== 'superseded');
}

export function candidateLabel(row, decisionNames, fmt) {
  const prefix = row.origin === 'manual' ? '[직접 추가] ' : '';
  return `${prefix}${row.video_id} · ${fmt(row.start_s)}–${fmt(row.end_s)}초 · ${decisionNames[row.status]}`;
}

export function queueSummaryText(rows, blind) {
  const counts = ['pending', 'quality', 'history'].map(filter => queueRows(rows, filter, blind).length);
  const manual = rows.filter(row => row.origin === 'manual').length;
  return `일반 ${counts[0]} · 품질 확인 ${counts[1]} · 처리 이력 ${counts[2]} · 직접 추가 ${manual}`;
}

export function normalizeBoundsMs(start, end, duration) {
  const startMs = Math.max(0, Math.round(start * 1000));
  const rawEndMs = Math.round(end * 1000);
  const maxEndMs = Number.isFinite(duration) && duration > 0
    ? Math.floor(duration * 1000 + 1e-9)
    : rawEndMs;
  return { start_s: startMs / 1000, end_s: Math.min(rawEndMs, maxEndMs) / 1000 };
}

function suggestionsVisible(row, blind, region, transcript) {
  if (blind || !row?.model_suggestions || row.origin === 'manual' || row.status !== 'pending' || !region ||
      transcript !== row.asr_transcript ||
      Math.round(region.start * 1000) !== Math.round(row.start_s * 1000) ||
      Math.round(region.end * 1000) !== Math.round(row.end_s * 1000)) return false;
  return true;
}

export function suggestionText(row, blind, region, transcript, labels) {
  if (!suggestionsVisible(row, blind, region, transcript)) return '';
  return [['text', '대사'], ['audio', '음성'], ['combined', '결합']].map(([kind, name]) => {
    const result = row.model_suggestions[kind];
    const value = result.status === 'ok' ? labels[result.label] || result.label
      : result.status === 'failed' ? '실패' : '판단 보류';
    const scores = result.probabilities;
    const detail = scores ? Object.entries(scores).map(([label, score]) =>
      `${label === 'other' ? '기타' : labels[label] || label} ${(score * 100).toFixed(1)}%`).join(', ') : '';
    return `${name} · ${value}` + (result.reason ? ` (${result.reason})` : '') +
      (detail ? ` [${detail}]` : '');
  }).join(' / ');
}

const SCORE_LABELS = [['neutral', '중립'], ['angry', '화남'], ['sad', '슬픔'], ['happy', '행복'], ['other', '기타']];
const MODEL_REASONS = {
  unsupported_emotion: '기타 감정 점수가 높음', tied_scores: '최고 점수 동점',
  missing_valid_scores: '유효한 모델 점수 부족', missing_valid_prediction: '유효한 모델 제안 부족',
  model_disagreement: '대사·음성 판단 불일치', timeout: '시간 초과',
  model_unavailable: '모델 연결 확인 필요', invalid_output: '점수 형식 확인 필요',
  inference_failed: '추론 실패', missing_input: '입력 자료 없음',
};

export function suggestionView(row, blind, region, transcript) {
  if (!suggestionsVisible(row, blind, region, transcript)) return null;
  const results = row.model_suggestions;
  const mean = results.combined?.model_id === 'mean-score';
  const columns = [['text', '대사'], ['audio', '음성'], ['combined', mean ? '평균' : '결합']];
  const labelName = label => SCORE_LABELS.find(([key]) => key === label)?.[1] || '판단 보류';
  const outcome = result => result?.status === 'ok' ? labelName(result.label)
    : result?.status === 'failed' ? '실행 실패' : '판단 보류';
  const combined = results.combined;
  return {
    title: `${mean ? '평균' : '결합'} 제안: ${outcome(combined)}`,
    reason: combined?.reason ? MODEL_REASONS[combined.reason] || '직접 확인이 필요합니다' : '',
    note: mean ? '대사 50% + 음성 50% · 참고 점수이며 최종 감정은 직접 선택하세요.'
      : '모델 참고 제안 · 최종 감정은 직접 선택하세요.',
    columns: columns.map(([, name]) => name),
    outcomes: columns.map(([kind, name]) => `${name}: ${outcome(results[kind])}`),
    rows: SCORE_LABELS.map(([label, name]) => ({
      label: name, proposed: combined?.status === 'ok' && combined.label === label,
      cells: columns.map(([kind]) => {
        const result = results[kind];
        const value = result?.status === 'failed' ? null : result?.probabilities?.[label];
        const valid = typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
        return {text: valid ? `${(value * 100).toFixed(1)}%` : '—',
          top: valid && value === Math.max(...Object.values(result.probabilities))};
      }),
    })),
  };
}

export function renderSuggestions(container, view) {
  container.replaceChildren();
  container.hidden = !view;
  if (!view) return;
  const doc = container.ownerDocument;
  const add = (parent, tag, text, className) => {
    const node = doc.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    parent.append(node);
    return node;
  };
  add(container, 'h3', view.title, 'suggestion-title');
  if (view.reason) add(container, 'p', view.reason, 'suggestion-reason');
  const table = add(container, 'table', undefined, 'suggestion-table');
  add(table, 'caption', '감정별 모델 참고 점수', 'sr-only');
  const header = add(add(table, 'thead'), 'tr');
  for (const name of ['감정', ...view.columns]) add(header, 'th', name).scope = 'col';
  const body = add(table, 'tbody');
  for (const row of view.rows) {
    const tr = add(body, 'tr', undefined, row.proposed ? 'is-proposed' : '');
    add(tr, 'th', row.label).scope = 'row';
    for (const cell of row.cells) add(tr, 'td', cell.text, cell.top ? 'is-top' : '');
  }
  add(container, 'p', view.outcomes.join(' · '), 'suggestion-outcomes');
  add(container, 'p', view.note, 'suggestion-note');
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
