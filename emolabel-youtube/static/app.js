import WaveSurfer from '/static/vendor/wavesurfer.esm.js';
import RegionsPlugin from '/static/vendor/regions.esm.js';
import { ActiveClock, bindPlaybackEvents, enforceSelection, isShortcutBlocked, playSelection, queueRows, reviewFields } from './review.mjs?v=playback2';

const $ = (sel) => document.querySelector(sel);

const el = {
  urlInput: $('#url-input'), loadBtn: $('#load-btn'), status: $('#status'),
  workspace: $('#workspace'), video: $('#video'),
  videoTitle: $('#video-title'), videoInfo: $('#video-info'),
  waveform: $('#waveform'),
  playBtn: $('#play-btn'), playRegionBtn: $('#play-region-btn'), loopToggle: $('#loop-toggle'),
  continueBtn: $('#continue-btn'),
  timeReadout: $('#time-readout'),
  regionStart: $('#region-start'), regionEnd: $('#region-end'), regionDur: $('#region-dur'),
  setStartBtn: $('#set-start-btn'), setEndBtn: $('#set-end-btn'),
  extractBtn: $('#extract-btn'), extractStatus: $('#extract-status'),
  labelPanel: $('#label-panel'), clipIdLabel: $('#clip-id-label'), qcLine: $('#qc-line'),
  transcript: $('#transcript'), asrInfo: $('#asr-info'),
  genderRadios: $('#gender-radios'), genderInfo: $('#gender-info'),
  emotionRadios: $('#emotion-radios'),
  actor: $('#actor'), notes: $('#notes'), annotatorId: $('#annotator-id'),
  saveBtn: $('#save-btn'), discardBtn: $('#discard-btn'), saveStatus: $('#save-status'),
  clipList: $('#clip-list'),
  confidence: $('#confidence'), quality: $('#quality'), ambiguous: $('#ambiguous'),
  verified: $('#verified'), decision: $('#decision'), reason: $('#reason'),
  sessionBtn: $('#session-btn'), queueBtn: $('#queue-btn'), blind: $('#blind-mode'),
  filter: $('#queue-filter'), preview: $('#preview'), cutVideo: $('#cut-video'), cutAudio: $('#cut-audio'),
};

const EXTRACT_TEXT = '구간 추출 및 사전 라벨';
const OVERWRITE_TEXT = '기존 구간을 덮어쓸까요?';
const REGION_COLOR = 'rgba(36, 84, 125, 0.16)';
const decisionNames = {
  pending: '미검수', accepted: '채택', accepted_edited: '수정 후 채택',
  hold: '보류', rejected: '제외', superseded: '경계 수정 전',
};

const state = {
  config: null,
  emotionLabel: {},   // value -> "English(한국어)" display text
  genderLabel: {},
  video: null,        // metadata from POST /api/videos
  ws: null,           // wavesurfer instance
  regions: null,      // regions plugin
  region: null,       // the single active region
  loop: false,
  playingOriginal: false,
  clipId: null,       // current extracted clip
  pendingOverwrite: false,
  busy: false,
  annotator: '', queue: false, rows: [], row: null, clock: null,
};

/* ---------- helpers ---------- */

function setStatus(node, text, cls = '') {
  node.textContent = text;
  node.className = `status ${cls}`.trim();
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch { detail = res.statusText; }
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join('; ');
    const err = new Error(detail || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

const fmt = (s) => s.toFixed(2);
const frameDur = () => 1 / (state.video?.fps || 30);

/* ---------- form rendering (from /api/config) ---------- */

function renderRadios(container, name, options) {
  container.innerHTML = '';
  for (const opt of options) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = name;
    input.value = opt.value;
    label.append(input, document.createTextNode(opt.label));
    container.append(label);
  }
}

function getRadio(name) {
  return document.querySelector(`input[name="${name}"]:checked`)?.value || null;
}

function setRadio(name, value) {
  for (const input of document.querySelectorAll(`input[name="${name}"]`)) {
    input.checked = input.value === value;
  }
}

/* ---------- video load ---------- */

async function loadVideo() {
  const url = el.urlInput.value.trim();
  if (!url || state.busy || !state.annotator || el.blind.checked) return;
  setBusy(true);
  setStatus(el.status, '영상을 불러오는 중…', 'busy');
  try {
    const meta = await api('/api/videos', { method: 'POST', body: JSON.stringify({ url }) });
    setQueueMode(false);
    state.video = meta;
    setStatus(el.status, `${meta.video_id} 불러오기 완료`, 'ok');
    el.workspace.classList.remove('hidden');
    el.videoTitle.textContent = meta.title || meta.video_id;
    el.videoInfo.textContent =
      `${meta.channel || ''} · ${fmt(meta.duration_s)}s · ${meta.width}×${meta.height} @ ${meta.fps}fps`;
    el.video.src = meta.media_url;
    resetLabelPanel();
    renderClipList(meta.clips);
    setStatus(el.status, '파형을 만드는 중…', 'busy');
    const peaks = await api(`/api/videos/${meta.video_id}/peaks`);
    buildWaveform(peaks);
    setStatus(el.status, '준비 완료 — 파형을 드래그해 발화 구간을 선택하세요', 'ok');
  } catch (e) {
    setStatus(el.status, e.message, 'error');
  } finally {
    setBusy(false);
  }
}

function buildWaveform(peaksData) {
  if (state.ws) { state.ws.destroy(); state.ws = null; }
  state.region = null;
  updateRegionReadout();

  const ws = WaveSurfer.create({
    container: el.waveform,
    media: el.video,
    peaks: [peaksData.peaks],
    duration: peaksData.duration_s,
    height: 110,
    waveColor: '#9ba2a6',
    progressColor: '#24547d',
    cursorColor: '#20201d',
    cursorWidth: 1,
  });
  const regions = ws.registerPlugin(RegionsPlugin.create());
  regions.enableDragSelection({ color: REGION_COLOR });

  regions.on('region-created', (region) => {
    for (const r of regions.getRegions()) if (r !== region) r.remove();
    state.region = region;
    updateRegionReadout();
  });
  regions.on('region-updated', (region) => {
    state.region = region;
    updateRegionReadout();
  });
  ws.on('timeupdate', (t) => {
    el.timeReadout.textContent = `${fmt(t)} / ${fmt(peaksData.duration_s)}`;
    if (!state.playingOriginal) enforceSelection(ws, state.region, t, state.loop);
  });
  bindPlaybackEvents(ws, state, el.playBtn);

  state.ws = ws;
  state.regions = regions;
  return ws;
}

/* ---------- region controls ---------- */

function togglePlayback() {
  if (!state.ws) return;
  if (state.ws.isPlaying()) state.ws.pause();
  else {
    state.playingOriginal = false;
    playSelection(state.ws, state.region);
  }
}

function updateRegionReadout() {
  const r = state.region;
  el.regionStart.textContent = r ? fmt(r.start) : '–';
  el.regionEnd.textContent = r ? fmt(r.end) : '–';
  el.regionDur.textContent = r ? fmt(r.end - r.start) : '–';
  el.playRegionBtn.disabled = !r;
  el.continueBtn.disabled = !r;
  el.extractBtn.disabled = !r || state.busy;
  state.pendingOverwrite = false;
  el.extractBtn.textContent = EXTRACT_TEXT;
  updateSaveEnabled();
}

function setRegionBounds(start, end) {
  if (!state.regions) return;
  const dur = state.video.duration_s;
  start = Math.max(0, Math.min(start, dur - 0.05));
  end = Math.max(start + 0.05, Math.min(end, dur));
  if (state.region) {
    state.region.setOptions({ start, end });
  } else {
    state.regions.addRegion({ start, end, color: REGION_COLOR });
  }
  state.region = state.regions.getRegions()[0];
  updateRegionReadout();
}

function nudge(spec) {
  if (!state.region) return;
  const [edge, amountStr] = spec.split(':');
  const amount = amountStr.endsWith('f')
    ? (amountStr.startsWith('-') ? -frameDur() : frameDur())
    : parseFloat(amountStr);
  const { start, end } = state.region;
  if (edge === 'start') setRegionBounds(start + amount, end);
  else setRegionBounds(start, end + amount);
}

/* ---------- extract + prelabel ---------- */

async function extractClip() {
  const r = state.region;
  if (!r || state.busy || state.queue || !state.annotator) return;
  setBusy(true);
  el.extractBtn.disabled = true;
  setStatus(el.extractStatus, '구간을 추출하는 중…', 'busy');
  try {
    const body = {
      start_s: Math.round(r.start * 1000) / 1000,
      end_s: Math.round(r.end * 1000) / 1000,
      overwrite: state.pendingOverwrite,
    };
    const res = await api(`/api/videos/${state.video.video_id}/extract`, {
      method: 'POST', body: JSON.stringify(body),
    });
    state.clipId = res.clip_id;
    state.pendingOverwrite = false;
    el.extractBtn.textContent = EXTRACT_TEXT;
    openLabelPanel(res.clip_id);
    setStatus(el.extractStatus, `구간 추출 완료 (${fmt(res.duration_s)}초) · 사전 라벨링 중…`, 'busy');
    await prelabel(res.clip_id);
    setStatus(el.extractStatus, '사전 라벨 완료 — 검토 후 저장하세요', 'ok');
  } catch (e) {
    if (e.status === 409 && e.message.endsWith('already exists')) {
      state.pendingOverwrite = true;
      el.extractBtn.textContent = OVERWRITE_TEXT;
      setStatus(el.extractStatus, '이미 추출된 구간입니다. 한 번 더 누르면 덮어씁니다.', 'error');
    } else {
      setStatus(el.extractStatus, e.message, 'error');
    }
  } finally {
    setBusy(false);
  }
}

async function prelabel(clipId) {
  setStatus(el.asrInfo, '', '');
  el.asrInfo.textContent = '음성을 전사하는 중… (첫 실행 시 약 2.8GB 모델 다운로드)';
  el.genderInfo.textContent = '';
  const pre = await api(`/api/clips/${clipId}/prelabel`, { method: 'POST', body: '{}' });
  if (clipId !== state.clipId) return; // user moved on to another clip meanwhile

  el.transcript.value = pre.asr.text || '';
  el.asrInfo.textContent = pre.asr.text
    ? `(${pre.asr.model.split('/').pop()})`
    : '(음성이 감지되지 않았습니다 — 비워두거나 들리는 대로 입력하세요)';

  const g = pre.gender;
  const gLabel = state.genderLabel[g.label] || g.label;
  const conf = g.probs ? Math.max(...Object.values(g.probs)) : null;
  const lowConf = conf !== null && conf < state.config.gender_confidence_threshold;
  const preselect = (!pre.asr.text || lowConf || g.label === 'unknown') ? 'unknown' : g.label;
  setRadio('gender', preselect);
  el.genderInfo.textContent =
    g.probs ? `(모델: ${gLabel} ${conf.toFixed(2)})`
      : g.median_f0_hz ? `(F0 추정: ${gLabel}, ${g.median_f0_hz} Hz)`
        : '(모델 추정값 없음)';
  renderQC(pre.qc);
  updateSaveEnabled();
}

function renderQC(qc) {
  el.qcLine.innerHTML = '';
  const add = (cls, text) => {
    const s = document.createElement('span');
    s.className = cls;
    s.textContent = text;
    el.qcLine.append(s);
  };

  const f = qc?.face;
  if (f?.error) add('qc-dim', '얼굴 확인 불가');
  else if (f?.verdict === 'single') add('qc-ok', '✓ 얼굴 1명');
  else if (f?.verdict === 'none') add('qc-warn', '⚠ 얼굴 미검출');
  else if (f?.verdict === 'multiple') {
    const n = Math.max(...f.faces_per_frame);
    add('qc-warn', `⚠ 얼굴 최대 ${n}명`);
  }

  const s = qc?.speaker;
  if (s?.error) add('qc-dim', '화자 확인 불가');
  else if (s?.verdict === 'single') add('qc-ok', '✓ 화자 1명');
  else if (s?.verdict === 'no_speech') add('qc-warn', '⚠ 음성 없음');
  else if (s?.verdict === 'multiple') {
    add('qc-warn', `⚠ 화자 약 ${s.estimated_speakers}명`);
  }

  const v = qc?.vad;
  if (v && !v.error) {
    const pct = Math.round(v.speech_ratio * 100);
    add(pct >= 50 ? 'qc-ok' : 'qc-warn', `음성 비율 ${pct}%`);
  }

  el.qcLine.classList.toggle('hidden', !el.qcLine.childNodes.length);
}

/* ---------- label panel ---------- */

function openLabelPanel(clipId) {
  el.labelPanel.classList.remove('disabled');
  el.clipIdLabel.textContent = clipId;
  el.qcLine.classList.add('hidden');
  el.qcLine.innerHTML = '';
  el.transcript.value = '';
  el.asrInfo.textContent = '';
  el.genderInfo.textContent = '';
  setRadio('gender', 'unknown');
  setRadio('emotion', null);
  el.actor.value = '';
  el.notes.value = '';
  el.confidence.value = '';
  el.quality.value = '';
  el.ambiguous.value = '';
  el.decision.value = '';
  el.reason.value = '';
  el.verified.checked = false;
  setStatus(el.saveStatus, '');
  updateSaveEnabled();
}

function resetLabelPanel() {
  state.clipId = null;
  el.labelPanel.classList.add('disabled');
  el.clipIdLabel.textContent = '';
  setStatus(el.extractStatus, '');
}

function updateSaveEnabled() {
  const status = state.queue ? el.decision.value : 'accepted';
  const accepts = status === 'accepted' || status === 'accepted_edited';
  const hasRatings = el.confidence.value && el.quality.value && el.ambiguous.value;
  const ready = state.annotator && state.clipId && !state.busy && status &&
    (!state.queue || ((!accepts || state.region) && state.row?.status !== 'superseded')) &&
    (status === 'accepted' || el.reason.value.trim()) &&
    (!accepts || (hasRatings && el.verified.checked && getRadio('emotion') && el.transcript.value.trim()));
  el.saveBtn.disabled = !ready;
}

async function saveAnnotation() {
  if (el.saveBtn.disabled || state.busy) return;
  if (state.queue) return saveReview();
  setBusy(true);
  setStatus(el.saveStatus, '저장하는 중…', 'busy');
  try {
    const body = {
      emotion: getRadio('emotion'),
      valence: null, arousal: null,
      gender: getRadio('gender') || 'unknown',
      transcript: el.transcript.value,
      actor: el.actor.value,
      notes: [`CONF=${el.confidence.value}`, `QUALITY=${el.quality.value}`,
        `AMBIG=${el.ambiguous.value}`, el.notes.value.trim()].filter(Boolean).join('; '),
      confidence: Number(el.confidence.value), quality: el.quality.value,
      ambiguous: Number(el.ambiguous.value), verified: el.verified.checked,
      annotator_id: state.annotator,
    };
    await api(`/api/clips/${state.clipId}/annotation`, {
      method: 'POST', body: JSON.stringify(body),
    });
    setStatus(el.saveStatus, `${state.clipId} 저장 완료`, 'ok');
    setStatus(el.extractStatus, '저장했습니다. 다음 발화 구간을 선택하세요.', 'ok');
    resetLabelPanel();
    await refreshClipList();
  } catch (e) {
    setStatus(el.saveStatus, e.message, 'error');
  } finally {
    setBusy(false);
  }
}

async function discardClip() {
  if (!state.clipId || state.busy || state.queue) return;
  setBusy(true);
  try {
    await api(`/api/clips/${state.clipId}`, { method: 'DELETE' });
    resetLabelPanel();
    await refreshClipList();
    setStatus(el.extractStatus, '구간을 폐기했습니다', 'ok');
  } catch (e) {
    setStatus(el.extractStatus, e.message, 'error');
  } finally {
    setBusy(false);
  }
}

/* ---------- clip list ---------- */

function renderClipList(clips) {
  el.clipList.innerHTML = '';
  for (const c of clips) {
    const li = document.createElement('li');
    const range = document.createElement('span');
    range.className = 'range';
    range.textContent = `${fmt(c.start_s)} – ${fmt(c.end_s)}`;
    const tag = document.createElement('span');
    tag.className = c.annotated ? 'tag done' : 'tag';
    tag.textContent = c.annotated
      ? (state.emotionLabel[c.emotion] || c.emotion || '라벨 완료')
      : '미라벨';
    li.append(range, tag);
    li.addEventListener('click', () => {
      setRegionBounds(c.start_s, c.end_s);
      state.ws?.setTime(c.start_s);
    });
    el.clipList.append(li);
  }
  if (!clips.length) {
    const li = document.createElement('li');
    li.className = 'dim';
    li.textContent = '저장된 구간이 없습니다';
    el.clipList.append(li);
  }
}

async function refreshClipList() {
  if (!state.video) return;
  renderClipList(await api(`/api/clips?video_id=${state.video.video_id}`));
}

/* ---------- batch review ---------- */

function setBusy(busy) {
  state.busy = busy;
  el.workspace.inert = busy;
  el.sessionBtn.disabled = busy;
  el.queueBtn.disabled = busy || !state.annotator;
  el.loadBtn.disabled = busy || !state.annotator || el.blind.checked;
  el.filter.disabled = busy;
  el.extractBtn.disabled = busy || !state.region;
  updateClock();
  updateSaveEnabled();
}

function updateClock() {
  if (!state.clock) return;
  if (state.busy || document.hidden || !document.hasFocus()) state.clock.pause();
  else state.clock.resume();
  $('#active-seconds').textContent = Math.floor(state.clock.seconds());
}

function clearMedia() {
  state.playingOriginal = false;
  el.video.pause();
  state.ws?.destroy();
  state.ws = null;
  state.region = null;
  state.regions = null;
  state.video = null;
  for (const player of [el.video, el.cutVideo, el.cutAudio]) {
    player.pause();
    player.removeAttribute('src');
    player.load();
  }
  el.preview.open = false;
  el.playBtn.textContent = '▶ 재생';
  el.timeReadout.textContent = '';
  updateRegionReadout();
}

function setQueueMode(enabled) {
  state.queue = enabled;
  state.row = null;
  state.clock = null;
  clearMedia();
  resetLabelPanel();
  $('#queue-controls').classList.toggle('hidden', !enabled);
  $('#review-decision').classList.toggle('hidden', !enabled);
  $('#extract-row').classList.toggle('hidden', enabled);
  el.discardBtn.classList.toggle('hidden', enabled);
  el.preview.classList.add('hidden');
  $('#list-title').textContent = enabled ? '검수 후보' : '현재 영상의 구간';
  $('#queue-count').textContent = '';
}

function toggleSession() {
  if (state.busy) return;
  if (state.annotator) {
    setQueueMode(false);
    state.rows = [];
    state.annotator = '';
    el.annotatorId.value = '';
    el.blind.checked = false;
    el.workspace.classList.add('hidden');
    setStatus(el.status, '세션을 종료했습니다.');
  } else {
    state.annotator = el.annotatorId.value.trim();
    if (!state.annotator) {
      setStatus(el.status, '세션을 시작하려면 본인 ID를 입력하세요.', 'error');
      el.annotatorId.focus();
      return;
    }
    setStatus(el.status, el.blind.checked ? '독립 정답 세션을 시작했습니다.' : '검수 세션을 시작했습니다.', 'ok');
  }
  el.annotatorId.disabled = Boolean(state.annotator);
  el.blind.disabled = Boolean(state.annotator);
  el.sessionBtn.textContent = state.annotator ? '세션 종료' : '세션 시작';
  setBusy(false);
}

function renderQueue() {
  const rows = queueRows(state.rows, el.filter.value, el.blind.checked);
  el.clipList.replaceChildren();
  const counts = ['pending', 'quality', 'history'].map(filter => queueRows(state.rows, filter, el.blind.checked).length);
  $('#queue-count').textContent = `일반 ${counts[0]} · 품질 확인 ${counts[1]} · 처리 이력 ${counts[2]}`;
  for (const row of rows) {
    const li = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('aria-current', String(state.row?.clip_id === row.clip_id));
    button.textContent = `${row.video_id} · ${fmt(row.start_s)}–${fmt(row.end_s)}초 · ${decisionNames[row.status]}`;
    button.addEventListener('click', () => selectCandidate(row));
    li.append(button);
    el.clipList.append(li);
  }
  if (!rows.length) {
    const message = document.createElement('li');
    message.textContent = '이 목록에 후보가 없습니다.';
    el.clipList.append(message);
  }
  return rows;
}

function showHistory(row) {
  const history = $('#history');
  history.open = false;
  history.hidden = el.blind.checked;
  const list = history.querySelector('ol');
  list.replaceChildren();
  if (el.blind.checked) return;
  for (const entry of row.history || []) {
    const item = document.createElement('li');
    item.textContent = [decisionNames[entry.status], entry.annotator_id, entry.reason, entry.transcript,
      state.emotionLabel[entry.emotion], entry.active_seconds != null ? `${entry.active_seconds}초` : ''].filter(Boolean).join(' · ');
    list.append(item);
  }
  if (row.parent_clip_id) {
    const item = document.createElement('li');
    item.textContent = `이전 구간 ${row.parent_clip_id}`;
    list.prepend(item);
  }
}

async function openCandidate(row) {
  state.row = row || null;
  state.clock = null;
  clearMedia();
  resetLabelPanel();
  renderQueue();
  el.preview.classList.toggle('hidden', !row);
  if (!row) {
    el.videoTitle.textContent = '검수 후보 없음';
    el.videoInfo.textContent = '';
    return;
  }
  state.video = { video_id: row.video_id, duration_s: row.source_duration_s, fps: row.fps };
  state.clipId = row.clip_id;
  el.videoTitle.textContent = row.video_id;
  el.videoInfo.textContent = `원본 ${fmt(row.source_duration_s)}초 · ${decisionNames[row.status]}`;
  if (row.source_url) el.video.src = row.source_url;
  el.cutVideo.src = row.media_url;
  el.cutAudio.src = row.audio_url;
  openLabelPanel(row.clip_id);
  const fields = reviewFields(row, el.blind.checked);
  el.transcript.value = fields.transcript;
  el.transcript.placeholder = el.blind.checked ? '직접 들은 대사를 입력하세요' : '들리는 대로 확인하고 수정하세요';
  for (const name of ['actor', 'notes', 'reason', 'confidence', 'quality', 'ambiguous']) {
    el[name].value = fields[name] ?? '';
  }
  setRadio('emotion', fields.emotion);
  setRadio('gender', fields.gender);
  if (!el.blind.checked && row.quality_flags?.length) {
    el.qcLine.textContent = row.quality_flags.join(' · ');
    el.qcLine.classList.remove('hidden');
  }
  showHistory(row);
  $('#active-seconds').textContent = '0';
  if (!row.source_url) {
    if (row.status !== 'superseded') state.clock = new ActiveClock();
    el.videoInfo.textContent += ' · 원본 변경 또는 누락';
    updateClock();
    updateSaveEnabled();
    return;
  }
  let peaks;
  try {
    peaks = await api(`/api/videos/${row.video_id}/peaks`);
  } catch (e) {
    if (row.status !== 'superseded') state.clock = new ActiveClock();
    el.videoInfo.textContent += ' · 파형 불러오기 실패';
    updateClock();
    updateSaveEnabled();
    throw e;
  }
  const ws = buildWaveform(peaks);
  ws.once('ready', () => {
    if (state.ws !== ws) return;
    setRegionBounds(row.start_s, row.end_s);
    ws.setTime(row.start_s);
    if (row.status !== 'superseded') state.clock = new ActiveClock();
    updateClock();
  });
  ws.on('error', () => {
    if (state.ws !== ws) return;
    if (row.status !== 'superseded' && !state.clock) state.clock = new ActiveClock();
    updateClock();
    updateSaveEnabled();
    setStatus(el.status, '원본 영상을 재생할 수 없습니다. 배치 미디어를 확인하세요.', 'error');
  });
}

async function selectCandidate(row) {
  if (state.busy) return;
  setBusy(true);
  try {
    await openCandidate(row);
    setStatus(el.status, row ? '후보를 열었습니다.' : '다른 검수 목록을 선택할 수 있습니다.');
  } catch (e) {
    setStatus(el.status, e.message, 'error');
  } finally {
    setBusy(false);
  }
}

async function startQueue() {
  if (state.busy || !state.annotator) return;
  setBusy(true);
  try {
    const rows = await api('/api/review/candidates');
    setQueueMode(true);
    state.rows = rows;
    el.workspace.classList.remove('hidden');
    const available = renderQueue();
    await openCandidate(available[0]);
    setStatus(el.status, rows.length ? '검수 목록을 불러왔습니다.' : '배치를 실행해 검수 후보를 생성하세요.');
  } catch (e) {
    setStatus(el.status, e.message, 'error');
  } finally {
    setBusy(false);
  }
}

async function saveReview() {
  const row = state.row;
  if (!row || !state.clock || state.busy || el.saveBtn.disabled) return;
  setBusy(true);
  const roundMs = seconds => Math.round(seconds * 1000) / 1000;
  const body = {
    status: el.decision.value, revision: row.revision, source_version: row.source_version,
    start_s: roundMs(state.region?.start ?? row.start_s), end_s: roundMs(state.region?.end ?? row.end_s),
    reason: el.reason.value.trim(), annotator_id: state.annotator, active_seconds: state.clock.seconds(),
    transcript: el.transcript.value.trim(), emotion: getRadio('emotion'), gender: getRadio('gender') || 'unknown',
    actor: el.actor.value.trim(), notes: el.notes.value.trim(),
    confidence: el.confidence.value ? Number(el.confidence.value) : null, quality: el.quality.value || null,
    ambiguous: el.ambiguous.value ? Number(el.ambiguous.value) : null, verified: el.verified.checked, blind: el.blind.checked,
  };
  try {
    await api(`/api/review/candidates/${row.clip_id}`, { method: 'POST', body: JSON.stringify(body) });
  } catch (e) {
    setStatus(el.saveStatus, e.message, 'error');
    setBusy(false);
    return;
  }
  // The POST is committed. Never resubmit a decision if refreshing the list fails.
  state.clock = null;
  state.row = null;
  resetLabelPanel();
  try {
    state.rows = await api('/api/review/candidates');
    if (el.filter.value === 'history') {
      el.filter.value = state.rows.some(r => r.status === 'pending' && !r.low_quality) ? 'pending' : 'quality';
    }
    await openCandidate(renderQueue()[0]);
    setStatus(el.status, `${row.clip_id} 검수 결과를 저장했습니다.`, 'ok');
  } catch (e) {
    state.rows = [];
    await openCandidate(null);
    setStatus(el.status, `저장 완료. 목록을 다시 불러오세요. ${e.message}`, 'error');
  } finally {
    setBusy(false);
  }
}

/* ---------- wiring ---------- */

async function init() {
  state.config = await api('/api/config');
  state.emotionLabel = Object.fromEntries(state.config.emotions.map((o) => [o.value, o.label]));
  state.genderLabel = Object.fromEntries(state.config.gender_options.map((o) => [o.value, o.label]));
  renderRadios(el.genderRadios, 'gender', state.config.gender_options);
  renderRadios(el.emotionRadios, 'emotion', state.config.emotions);
  el.annotatorId.value = '';
  el.sessionBtn.addEventListener('click', toggleSession);
  el.annotatorId.addEventListener('keydown', event => {
    if (event.key === 'Enter') toggleSession();
  });
  el.queueBtn.addEventListener('click', startQueue);
  el.filter.addEventListener('change', () => selectCandidate(renderQueue()[0]));
  for (const eventName of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'input']) {
    document.addEventListener(eventName, () => state.clock?.activity(), { passive: true });
  }
  document.addEventListener('visibilitychange', updateClock);
  window.addEventListener('blur', () => state.clock?.pause());
  window.addEventListener('focus', updateClock);
  window.setInterval(updateClock, 1000);
  for (const player of [el.video, el.cutVideo, el.cutAudio]) {
    player.addEventListener('play', () => {
      for (const other of [el.video, el.cutVideo, el.cutAudio]) if (other !== player) other.pause();
    });
  }

  el.loadBtn.addEventListener('click', loadVideo);
  el.urlInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') loadVideo(); });

  el.playBtn.addEventListener('click', togglePlayback);
  el.video.addEventListener('click', togglePlayback);
  el.playRegionBtn.addEventListener('click', () => {
    if (!state.ws || !state.region) return;
    state.playingOriginal = false;
    playSelection(state.ws, state.region, true);
  });
  el.continueBtn.addEventListener('click', () => {
    if (!state.ws || !state.region) return;
    state.playingOriginal = true;
    state.ws.play();
  });
  el.loopToggle.addEventListener('change', () => { state.loop = el.loopToggle.checked; });

  el.setStartBtn.addEventListener('click', () => {
    if (state.ws) setRegionBounds(state.ws.getCurrentTime(), state.region?.end ?? state.ws.getCurrentTime() + 1);
  });
  el.setEndBtn.addEventListener('click', () => {
    if (state.ws && state.region) setRegionBounds(state.region.start, state.ws.getCurrentTime());
  });
  for (const btn of document.querySelectorAll('[data-nudge]')) {
    btn.addEventListener('click', () => nudge(btn.dataset.nudge));
  }

  el.extractBtn.addEventListener('click', extractClip);
  el.saveBtn.addEventListener('click', saveAnnotation);
  el.discardBtn.addEventListener('click', discardClip);
  el.labelPanel.addEventListener('input', updateSaveEnabled);
  el.labelPanel.addEventListener('change', updateSaveEnabled);

  document.addEventListener('keydown', (e) => {
    if (state.busy || e.altKey || e.ctrlKey || e.metaKey || isShortcutBlocked(document.activeElement, e.code)) return;
    if (e.code === 'Space') { e.preventDefault(); togglePlayback(); }
    else if (e.key === 'l' || e.key === 'L') { el.loopToggle.checked = !el.loopToggle.checked; state.loop = el.loopToggle.checked; }
    else if (e.key === '[') el.setStartBtn.click();
    else if (e.key === ']') el.setEndBtn.click();
    else if (state.clipId && state.row?.status !== 'superseded' && /^[1-4]$/.test(e.key)) {
      setRadio('emotion', state.config.emotions[Number(e.key) - 1].value);
      updateSaveEnabled();
    }
  });
}

init().catch((e) => setStatus(el.status, `초기화 실패: ${e.message}`, 'error'));
