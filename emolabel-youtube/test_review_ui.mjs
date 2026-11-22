import assert from 'node:assert/strict';
import test from 'node:test';
import {
  ActiveClock, bindPlaybackEvents, canStartAddition, canTranscribeCandidate,
  canUseEmotionShortcut, candidateLabel, enforceSelection, isShortcutBlocked,
  normalizeBoundsMs, playSelection, queueRows, queueSummaryText,
  renderSuggestions, restoreReviewFields, reviewFields, suggestionText,
  suggestionView,
} from './static/review.mjs';
import { EventEmitter } from 'node:events';

test('suggestions are display-only, disappear for edited inputs and stay hidden in blind mode', () => {
  const row = { start_s: 1, end_s: 4, asr_transcript: '대사', status: 'pending',
    model_suggestions: { text: { status: 'ok', label: 'sad' }, audio: { status: 'failed', reason: 'timeout' },
      combined: { status: 'abstained' } } };
  const region = { start: 1, end: 4 };
  const labels = { sad: '슬픔' };
  const display = suggestionText(row, false, region, '대사', labels);
  assert.match(display, /슬픔/);
  assert.match(display, /실패/);
  assert.match(display, /보류/);
  assert.equal(reviewFields(row, false).emotion, null);
  assert.equal(suggestionText(row, true, region, '대사', labels), '');
  assert.equal(suggestionText(row, false, region, '새 대사', labels), '');
  assert.equal(suggestionText(row, false, { start: 1, end: 5 }, '대사', labels), '');
});

test('score table compares columns without selecting a human label and masks stale suggestions', () => {
  const row = {start_s: 1, end_s: 4, asr_transcript: '대사', status: 'pending', model_suggestions: {
    text: {status: 'ok', label: 'neutral', probabilities: {neutral: .565, angry: .042, sad: .018, happy: .031, other: .344}},
    audio: {status: 'failed', reason: 'timeout', probabilities: {angry: 1}},
    combined: {model_id: 'mean-score', status: 'abstained', reason: 'missing_valid_scores'},
  }};
  const region = {start: 1, end: 4};
  const before = JSON.stringify(row);
  const view = suggestionView(row, false, region, '대사');
  assert.equal(view.title, '평균 제안: 판단 보류');
  assert.equal(view.reason, '유효한 모델 점수 부족');
  assert.deepEqual(view.columns, ['대사', '음성', '평균']);
  assert.equal(view.rows[0].cells[0].text, '56.5%');
  assert.equal(view.rows[0].cells[0].top, true);
  assert.equal(view.rows[1].cells[1].text, '—');
  assert.equal(view.rows[0].cells[2].text, '—');
  assert.equal(suggestionView(row, true, region, '대사'), null);
  assert.equal(suggestionView(row, false, region, '수정'), null);
  assert.equal(suggestionView(row, false, {start: 1, end: 5}, '대사'), null);
  assert.equal(JSON.stringify(row), before);
  assert.equal(reviewFields(row, false).emotion, null);
});

test('table renderer uses semantic cells, highlights the proposal and clears hidden content', () => {
  const doc = {createElement: tag => ({tag, children: [], ownerDocument: doc,
    append(child) {this.children.push(child);},
    replaceChildren() {this.children = [];},
  })};
  const container = doc.createElement('section');
  const row = {start_s: 1, end_s: 4, asr_transcript: '대사', status: 'pending', model_suggestions: {}};
  for (const kind of ['text', 'audio', 'combined']) row.model_suggestions[kind] = {
    model_id: kind === 'combined' ? 'mean-score' : kind, status: 'ok', label: 'angry',
    probabilities: {neutral: .283, angry: .521, sad: .009, happy: .016, other: .171},
  };
  const view = suggestionView(row, false, {start: 1, end: 4}, '대사');
  renderSuggestions(container, view);
  assert.equal(container.hidden, false);
  assert.equal(container.children[0].textContent, '평균 제안: 화남');
  const table = container.children.find(child => child.tag === 'table');
  const head = table.children.find(child => child.tag === 'thead');
  assert.equal(head.children[0].children[0].scope, 'col');
  const body = table.children.find(child => child.tag === 'tbody');
  assert.equal(body.children.length, 5);
  assert.equal(body.children[1].className, 'is-proposed');
  assert.equal(body.children[1].children[3].textContent, '52.1%');
  assert.equal(body.children[1].children[0].scope, 'row');
  view.title = '<img src=x onerror=alert(1)>';
  renderSuggestions(container, view);
  assert.equal(container.children[0].textContent, view.title);
  assert.equal(container.children[0].children.length, 0);
  renderSuggestions(container, null);
  assert.equal(container.hidden, true);
  assert.equal(container.children.length, 0);
});

test('pause before finish repeats only a looped selection', () => {
  const ws = new EventEmitter();
  const seeks = [];
  let plays = 0;
  ws.getCurrentTime = () => 10;
  ws.setTime = time => seeks.push(time);
  ws.play = () => { plays++; };
  const state = { playingOriginal: true, loop: true, region: { start: 2, end: 10 } };
  bindPlaybackEvents(ws, state, {});
  ws.emit('pause');
  ws.emit('finish');
  assert.deepEqual(seeks, []);
  assert.equal(plays, 0);
  assert.equal(state.playingOriginal, false);
  ws.emit('pause');
  ws.emit('finish');
  assert.deepEqual(seeks, [2]);
  assert.equal(plays, 1);
});

test('playback stops at the edited end, replays, resumes and loops within the selection', () => {
  let time = 0;
  let playing = false;
  const ws = {
    getCurrentTime: () => time, setTime: value => { time = value; },
    isPlaying: () => playing, play: () => { playing = true; }, pause: () => { playing = false; },
  };
  const region = { start: 2, end: 5 };
  playSelection(ws, region);
  assert.equal(time, 2);
  region.end = 4;
  enforceSelection(ws, region, 4.02, false);
  assert.deepEqual([time, playing], [4, false]);
  playSelection(ws, region);
  assert.deepEqual([time, playing], [2, true]);
  time = 3;
  ws.pause();
  playSelection(ws, region);
  assert.equal(time, 3);
  enforceSelection(ws, region, 4, true);
  assert.deepEqual([time, playing], [2, true]);
  time = 6;
  playSelection(ws, null);
  enforceSelection(ws, null, time, false);
  assert.deepEqual([time, playing], [6, true]);
});

test('sub-frame media clock jitter does not loop-seek at start or stall replay at end', () => {
  let time = 3.995;
  let playing = false;
  let seeks = 0;
  const ws = {
    getCurrentTime: () => time,
    setTime: value => { seeks += 1; time = value; },
    isPlaying: () => playing,
    play: () => { playing = true; },
    pause: () => { playing = false; },
  };
  const region = { start: 2, end: 4 };
  // Stopped 5ms shy of region.end due to video frame quantization -> replay jumps to start.
  playSelection(ws, region);
  assert.deepEqual([time, playing, seeks], [2, true, 1]);
  // Video reports 5ms before region.start right after seek -> do not trigger another seek.
  enforceSelection(ws, region, 1.995, false);
  assert.equal(seeks, 1);
  // Seeking far before region.start still clamps back to region.start.
  enforceSelection(ws, region, 1.5, false);
  assert.deepEqual([time, seeks], [2, 2]);
});

test('idle cutoff, tab visibility and save latency are excluded from active time', () => {
  let now = 0;
  const clock = new ActiveClock(() => now);
  clock.resume();
  now = 95000;
  assert.equal(clock.seconds(), 90);
  clock.activity();
  now += 1000;
  clock.pause();
  now += 60000;
  clock.activity();
  assert.equal(clock.seconds(), 91);
  clock.resume();
  now += 2000;
  assert.equal(clock.seconds(), 93);
});

test('normal and quality queues partition pending clips and prioritize disagreement', () => {
  const a = { clip_id: 'a', status: 'pending' };
  const b = { clip_id: 'b', status: 'pending', model_disagreement: true };
  const c = { clip_id: 'c', status: 'hold', low_quality: true };
  const d = { clip_id: 'd', status: 'pending', low_quality: true };
  const rows = [a, b, c, d];
  assert.deepEqual(queueRows(rows, 'pending', false), [b, a]);
  assert.deepEqual(queueRows(rows, 'pending', true), [a, b]);
  assert.deepEqual(queueRows(rows, 'quality', false), [d]);
  assert.deepEqual(queueRows(rows, 'history', false), [c]);
});

test('fresh and blind reviews never inherit quality ratings or previous labels', () => {
  const row = { transcript: '초안', review: { transcript: '수정', emotion: 'sad', quality: 'B' } };
  assert.equal(reviewFields(row, false).transcript, '수정');
  assert.equal(reviewFields({ transcript: '초안' }, false).transcript, '초안');
  const blind = reviewFields(row, true);
  assert.equal(blind.transcript, '');
  assert.equal(blind.emotion, null);
  assert.equal(blind.quality, null);
  assert.equal(blind.verified, false);
});

test('shortcuts stay active after clicking checkboxes or radios but stay blocked in text fields', () => {
  assert.equal(isShortcutBlocked({ tagName: 'INPUT', type: 'text' }, 'Digit1'), true);
  assert.equal(isShortcutBlocked({ tagName: 'TEXTAREA' }, 'KeyL'), true);
  assert.equal(isShortcutBlocked({ tagName: 'SELECT' }, 'Space'), true);
  assert.equal(isShortcutBlocked({ tagName: 'INPUT', type: 'checkbox' }, 'Digit1'), false);
  assert.equal(isShortcutBlocked({ tagName: 'INPUT', type: 'radio' }, 'KeyL'), false);
  assert.equal(isShortcutBlocked({ tagName: 'INPUT', type: 'checkbox' }, 'Space'), true);
  assert.equal(isShortcutBlocked({ tagName: 'BUTTON' }, 'Space'), true);
  assert.equal(isShortcutBlocked({ tagName: 'BUTTON' }, 'Digit2'), false);
});

test('mean suggestions display scores and remain hidden for blind or edited reviews', () => {
  const probabilities = {neutral: 0.025, angry: 0, sad: 0.45, happy: 0.525, other: 0};
  const result = {status: 'ok', label: 'happy', probabilities};
  const row = {status: 'pending', start_s: 1, end_s: 4, asr_transcript: '초안',
    model_suggestions: {text: result, audio: result, combined: result}};
  const region = {start: 1, end: 4};
  assert.match(suggestionText(row, false, region, '초안', {}), /happy 52\.5%/);
  assert.match(suggestionText(row, false, region, '초안', {}), /기타 0\.0%/);
  assert.equal(suggestionText(row, true, region, '초안', {}), '');
  assert.equal(suggestionText(row, false, region, '수정', {}), '');
  assert.equal(suggestionText(row, false, {start: 1, end: 3}, '초안', {}), '');
});

test('manual addition guards shortcuts, supports url-loaded videos and hides suggestions', () => {
  const clock = new ActiveClock(() => 5000);
  const row = {
    clip_id: 'DGwuCn-EDfQ_0001000_0004000', video_id: 'DGwuCn-EDfQ',
    start_s: 1, end_s: 4, status: 'pending', revision: 0, source_version: '100:200',
    asr_transcript: '자동 대사', transcript: '자동 대사', review: null,
  };
  const draft = {
    revision: 0, source_version: '100:200', clock,
    bounds: { start: 1.2, end: 3.8 },
    fields: { transcript: '작성 중 대사', emotion: 'angry', verified: true, decision: 'accepted_edited', reason: '수정' },
  };
  const restored = restoreReviewFields(row, false, draft);
  assert.equal(restored.restored, true);
  assert.equal(restored.fields.transcript, '작성 중 대사');
  assert.equal(restored.fields.emotion, 'angry');
  assert.equal(restored.fields.verified, true);
  assert.equal(restored.fields.decision, 'accepted_edited');
  assert.deepEqual(restored.bounds, { start: 1.2, end: 3.8 });
  assert.equal(restored.clock, clock);
  assert.equal(row.transcript, '자동 대사');

  const stale = restoreReviewFields({ ...row, revision: 1 }, false, draft);
  assert.equal(stale.restored, false);
  assert.equal(stale.fields.transcript, '자동 대사');
  assert.equal(stale.fields.emotion, null);
  assert.equal(stale.bounds, null);

  const manualRow = {
    clip_id: 'DGwuCn-EDfQ_0006000_0009000', video_id: 'DGwuCn-EDfQ',
    start_s: 6, end_s: 9, status: 'pending', revision: 0, source_version: '100:200',
    origin: 'manual', asr_transcript: '', transcript: '', review: null,
    model_suggestions: { text: { status: 'ok', label: 'happy' }, audio: { status: 'ok', label: 'happy' }, combined: { status: 'ok', label: 'happy' } },
  };
  const freshManual = restoreReviewFields(manualRow, false, null);
  assert.equal(freshManual.restored, false);
  assert.equal(freshManual.fields.transcript, '');
  assert.equal(freshManual.fields.emotion, null);
  assert.equal(freshManual.fields.verified, false);
  assert.equal(suggestionView(manualRow, false, { start: 6, end: 9 }, ''), null);
  assert.equal(suggestionText(manualRow, false, { start: 6, end: 9 }, '', {}), '');

  assert.equal(canUseEmotionShortcut({ clipId: row.clip_id, addition: null, row }), true);
  assert.equal(canUseEmotionShortcut({ clipId: row.clip_id, addition: { context: {} }, row }), false);
  assert.equal(canUseEmotionShortcut({ clipId: row.clip_id, addition: null, row: { ...row, status: 'superseded' } }), false);

  assert.equal(canStartAddition({ busy: false, addition: null, annotator: 'a', video: {}, ws: {}, wsReady: true, queue: true, row: { source_url: '/media/v.mp4' } }), true);
  assert.equal(canStartAddition({ busy: false, addition: null, annotator: 'a', video: {}, ws: {}, wsReady: false, queue: true, row: { source_url: '/media/v.mp4' } }), false);
  assert.equal(canStartAddition({ busy: false, addition: null, annotator: 'a', video: {}, ws: {}, wsReady: true, queue: true, row: { source_url: null } }), false);
  assert.equal(canStartAddition({ busy: false, addition: null, annotator: 'a', video: {}, ws: {}, wsReady: true, queue: false, row: null }), true);
  assert.equal(canStartAddition({ busy: false, addition: {}, annotator: 'a', video: {}, ws: {}, wsReady: true, queue: false, row: null }), false);

  const names = { pending: '미검수', accepted: '채택' };
  assert.equal(candidateLabel(manualRow, names, s => s.toFixed(2)), '[직접 추가] DGwuCn-EDfQ · 6.00–9.00초 · 미검수');
  assert.equal(candidateLabel(row, names, s => s.toFixed(2)), 'DGwuCn-EDfQ · 1.00–4.00초 · 미검수');
  assert.equal(queueSummaryText([row, manualRow], false), '일반 2 · 품질 확인 0 · 처리 이력 0 · 직접 추가 1');

  assert.deepEqual(normalizeBoundsMs(6, 14.566667, 14.566667), { start_s: 6, end_s: 14.566 });
  assert.deepEqual(normalizeBoundsMs(1.2344, 5.0, 12.0), { start_s: 1.234, end_s: 5 });

  assert.equal(canTranscribeCandidate({ queue: true, busy: false, addition: null, row }, false), true);
  assert.equal(canTranscribeCandidate({ queue: true, busy: false, addition: null, row: manualRow }, false), true);
  assert.equal(canTranscribeCandidate({ queue: true, busy: false, addition: null, row: manualRow }, true), false);
  assert.equal(canTranscribeCandidate({ queue: true, busy: false, addition: {}, row: manualRow }, false), false);
  assert.equal(canTranscribeCandidate({ queue: true, busy: false, addition: null, row: { ...row, status: 'superseded' } }, false), false);
  assert.equal(canTranscribeCandidate({ queue: false, busy: false, addition: null, row: null }, false), false);
});
