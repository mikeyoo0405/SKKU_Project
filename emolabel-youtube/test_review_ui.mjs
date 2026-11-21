import assert from 'node:assert/strict';
import test from 'node:test';
import { ActiveClock, bindPlaybackEvents, enforceSelection, isShortcutBlocked, playSelection, queueRows, reviewFields } from './static/review.mjs';
import { EventEmitter } from 'node:events';

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
