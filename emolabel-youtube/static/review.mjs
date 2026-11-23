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
  const sceneTag = row.scene_warning?.status === 'has_cuts' ? ' · [장면 전환]' : '';
  return `${prefix}${row.video_id} · ${fmt(row.start_s)}–${fmt(row.end_s)}초 · ${decisionNames[row.status]}${sceneTag}`;
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

const SCENE_CUT_EPSILON = 0.001;

export function sceneWarningView(warning, region) {
  if (!warning || typeof warning !== 'object') return null;
  if (warning.status === 'not_analyzed') {
    if (warning.reason === 'scene_mode_off') return null;
    if (warning.reason === 'source_changed' || warning.reason === 'settings_changed') {
      return {
        tone: 'info',
        label: '장면 분석 재실행 필요',
        detail: '원본 영상 또는 분석 설정이 변경되었습니다. 배치를 다시 실행해 주세요.',
        cuts: [],
      };
    }
    return {
      tone: 'info',
      label: '장면 분석 미실행',
      detail: '이 영상은 아직 장면 전환 분석이 실행되지 않았습니다.',
      cuts: [],
    };
  }
  if (warning.status === 'unavailable') {
    return {
      tone: 'muted',
      label: '장면 분석 도구 미설치',
      detail: 'PySceneDetect가 설치되지 않아 장면 전환을 확인하지 않았습니다. 수동 검수는 계속할 수 있습니다.',
      cuts: [],
    };
  }
  if (warning.status === 'failed') {
    return {
      tone: 'muted',
      label: '장면 분석 실패',
      detail: '장면 전환 분석 중 오류가 발생했습니다. 전환 없음과 구분해 직접 확인하세요.',
      cuts: [],
    };
  }
  if (warning.status === 'has_cuts' || warning.status === 'no_cuts') {
    let cuts = Array.isArray(warning.cuts_s) ? warning.cuts_s : [];
    if (region && Array.isArray(warning.source_cuts_s) &&
        Number.isFinite(region.start) && Number.isFinite(region.end) && region.end > region.start) {
      cuts = warning.source_cuts_s.filter(
        cut => typeof cut === 'number' && Number.isFinite(cut) &&
          cut > region.start + SCENE_CUT_EPSILON && cut < region.end - SCENE_CUT_EPSILON,
      );
    }
    if (cuts.length) {
      return {
        tone: 'warn',
        label: `장면 전환 ${cuts.length}회 감지`,
        detail: '선택 구간 안에 장면 전환이 있습니다. 화자 전환·발화 잘림 여부는 영상과 원음으로 직접 확인하세요.',
        cuts,
      };
    }
    return {
      tone: 'ok',
      label: '선택 구간 내 장면 전환 없음',
      detail: 'PySceneDetect AdaptiveDetector 기준 이 구간 내부에 감지된 컷이 없습니다.',
      cuts: [],
    };
  }
  return null;
}

export function renderSceneWarning(container, view, onSeek) {
  container.replaceChildren();
  container.hidden = !view;
  container.className = view ? `scene-warning scene-${view.tone}` : 'scene-warning';
  if (!view) return;
  const doc = container.ownerDocument;
  const header = doc.createElement('div');
  header.className = 'scene-warning-header';
  const title = doc.createElement('strong');
  title.textContent = view.label;
  header.append(title);
  container.append(header);

  const detail = doc.createElement('p');
  detail.className = 'scene-warning-detail';
  detail.textContent = view.detail;
  container.append(detail);

  if (view.cuts.length) {
    const actions = doc.createElement('div');
    actions.className = 'scene-cut-list';
    for (const cut of view.cuts) {
      const btn = doc.createElement('button');
      btn.type = 'button';
      btn.className = 'scene-cut-btn';
      btn.textContent = `컷 ${cut.toFixed(2)}초로 이동`;
      if (typeof onSeek === 'function' && typeof btn.addEventListener === 'function') {
        btn.addEventListener('click', () => onSeek(cut));
      }
      actions.append(btn);
    }
    container.append(actions);
  }
}

const STAGE_LABELS = [
  ['download', '다운로드'],
  ['audio', '오디오 추출'],
  ['vad', 'VAD'],
  ['asr', 'ASR 전사'],
  ['scene', '장면 분석'],
  ['candidates', '후보 선정'],
  ['candidate_media', '클립 추출'],
];

function fmtSec(value) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}초` : '미측정';
}

function stageNote(info) {
  if (!info || typeof info !== 'object') return '미측정';
  const parts = [];
  if (info.executed_videos) parts.push(`실행 ${info.executed_videos}`);
  if (info.cached_videos) parts.push(`캐시 ${info.cached_videos}`);
  if (info.skipped_videos) parts.push(`건너뜀 ${info.skipped_videos}`);
  if (info.unavailable_videos) parts.push(`미설치 ${info.unavailable_videos}`);
  if (info.failed_videos) parts.push(`실패 ${info.failed_videos}`);
  if (info.unmeasured_videos) parts.push(`미측정 ${info.unmeasured_videos}`);
  return parts.length ? parts.join(' · ') : '미측정';
}

export function workspaceSummaryView(summary) {
  if (!summary || typeof summary !== 'object') return null;
  const rev = summary.review || {};
  const byOrigin = rev.by_origin || {};
  const byStatus = rev.by_status || {};
  const timing = rev.timing || {};
  const sceneCounts = rev.scene_warnings || {};
  const coverageLabel = timing.coverage === 'complete' ? '전체 기록됨'
    : timing.coverage === 'partial' ? '일부 미기록 포함' : '미측정';
  const acceptedLine = byStatus.accepted_edited
    ? `채택 ${byStatus.accepted_total || 0} (수정 후 채택 ${byStatus.accepted_edited})`
    : `채택 ${byStatus.accepted_total || 0}`;

  const reviewItems = [
    `전체 후보 ${rev.total_candidates || 0}건 (자동 ${byOrigin.automatic || 0} · 직접 추가 ${byOrigin.manual || 0})`,
    `미검수 ${byStatus.pending || 0} · ${acceptedLine} · 보류 ${byStatus.hold || 0} · 제외 ${byStatus.rejected || 0}`,
    timing.total_active_seconds == null
      ? `기록된 활성 검수 시간: 미측정 (${coverageLabel})`
      : `기록된 활성 검수 시간: ${timing.total_active_seconds.toFixed(1)}초 (${coverageLabel}) · ${
          timing.seconds_per_accepted == null ? '채택당 시간 산출 불가' : `채택당 ${timing.seconds_per_accepted.toFixed(1)}초`
        }`,
  ];
  if (rev.superseded_excluded) {
    reviewItems.push(`경계 수정 전 대체 후보 ${rev.superseded_excluded}건은 현재 후보·채택 수에서 제외`);
  }
  if (summary.scene_mode === 'warn') {
    const otherScene = (sceneCounts.not_analyzed || 0) + (sceneCounts.unavailable || 0) + (sceneCounts.failed || 0);
    reviewItems.push(`장면 경고: 전환 있음 ${sceneCounts.has_cuts || 0} · 전환 없음 ${sceneCounts.no_cuts || 0} · 미분석/미설치/실패 ${otherScene}`);
  }

  const batch = summary.latest_batch_run;
  let batchView;
  if (!batch) {
    batchView = {
      headline: '기록된 배치 실행 없음 (미측정)',
      note: '배치를 실행하면 이번 실행의 wall time과 단계별 실행/캐시 여부가 표시됩니다.',
      rows: [],
    };
  } else {
    const rtfText = typeof batch.batch_rtf === 'number' && Number.isFinite(batch.batch_rtf)
      ? ` · RTF ${batch.batch_rtf.toFixed(2)}x${
          typeof batch.processed_duration_seconds === 'number' && Number.isFinite(batch.processed_duration_seconds)
            ? ` (영상 ${batch.processed_duration_seconds.toFixed(1)}초)` : ''
        }`
      : typeof batch.executed_videos_rtf === 'number' && Number.isFinite(batch.executed_videos_rtf)
        ? ` · 영상별 처리 시간 기준 RTF ${batch.executed_videos_rtf.toFixed(2)}x`
        : ' · RTF 미산출';
    batchView = {
      headline: `마지막 배치 wall time: ${fmtSec(batch.batch_wall_seconds)} · 영상 ${batch.video_count || 0}개 · workers ${batch.video_workers || 1}${rtfText}`,
      note: `측정 범위: 이번 실행(${batch.finished_at || batch.started_at || '시각 미상'}). RTF는 이번에 발화 분석을 실행한 영상의 길이를 기준으로 계산합니다.`,
      rows: STAGE_LABELS.map(([key, label]) => {
        const info = batch.stage_summary?.[key];
        const timeText = info?.executed_seconds_sum != null
          ? fmtSec(info.executed_seconds_sum)
          : info?.cached_videos && !info?.executed_videos && !info?.failed_videos ? '캐시 사용' : '미측정';
        return { label, timeText, note: stageNote(info) };
      }),
    };
  }

  let emotionView = null;
  if (!summary.blind) {
    const emo = summary.latest_emotion_run;
    emotionView = emo
      ? {
          headline: `별도 감정 추론(${emo.run_tag || emo.run_id || '최근 실행'}): 후보 ${emo.candidates ?? '미측정'}건`,
          detail: `대사 ${fmtSec(emo.text_seconds_sum)} · 음성 ${fmtSec(emo.audio_seconds_sum)} · 합계 ${fmtSec(emo.combined_seconds_sum)} (수집 배치 wall time과 별도 실행)`,
        }
      : {
          headline: '별도 감정 추론 기록 없음',
          detail: '고정 후보 감정 추론을 실행하면 수집 배치 시간과 분리해 표시합니다.',
        };
  }

  return {
    scopeNote: summary.blind
      ? '현재 검수 목록 전체 현황 (독립 정답 모드 · 모델·감정 분포 비노출)'
      : '현재 검수 목록 전체 현황 (저장된 기록 기준 · 작성 중 시간은 저장 시 반영)',
    reviewItems,
    batchView,
    emotionView,
  };
}

export function renderWorkspaceSummary(container, view) {
  container.replaceChildren();
  if (!view) return;
  const doc = container.ownerDocument;
  const add = (parent, tag, text, className) => {
    const node = doc.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    parent.append(node);
    return node;
  };

  add(container, 'p', view.scopeNote, 'summary-scope');
  const grid = add(container, 'div', undefined, 'summary-grid');

  const reviewCard = add(grid, 'section', undefined, 'summary-card');
  add(reviewCard, 'h3', '현재 검수 현황 (S3)');
  const list = add(reviewCard, 'ul', undefined, 'summary-list');
  for (const item of view.reviewItems) add(list, 'li', item);

  const batchCard = add(grid, 'section', undefined, 'summary-card');
  add(batchCard, 'h3', '마지막 배치 처리 시간 (S2)');
  add(batchCard, 'p', view.batchView.headline, 'summary-headline');
  add(batchCard, 'p', view.batchView.note, 'summary-note');
  if (view.batchView.rows.length) {
    const table = add(batchCard, 'table', undefined, 'summary-table');
    const head = add(add(table, 'thead'), 'tr');
    for (const col of ['단계', '이번 실행 시간', '실행 · 캐시 상태']) add(head, 'th', col).scope = 'col';
    const body = add(table, 'tbody');
    for (const row of view.batchView.rows) {
      const tr = add(body, 'tr');
      add(tr, 'th', row.label).scope = 'row';
      add(tr, 'td', row.timeText);
      add(tr, 'td', row.note);
    }
  }
  if (view.emotionView) {
    const emoBox = add(batchCard, 'div', undefined, 'summary-emotion');
    add(emoBox, 'strong', view.emotionView.headline);
    add(emoBox, 'p', view.emotionView.detail, 'summary-note');
  }
}
