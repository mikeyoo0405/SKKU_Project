import assert from 'node:assert/strict';
import test from 'node:test';
import { enforceSelection } from './static/review.mjs';

test('R0: UI honors an ASR boundary even when synthetic speech continues beyond it', () => {
  let playing = true;
  let time = 4.99;
  const ws = { isPlaying: () => playing, pause: () => { playing = false; }, setTime: t => { time = t; } };
  const region = { start: 2, end: 5 }; // Synthetic VAD speech covers [1, 6].
  enforceSelection(ws, region, 4.99, false);
  assert.equal(playing, true);
  enforceSelection(ws, region, 5, false);
  assert.equal(playing, false);
  assert.equal(time, 5);
  // This establishes time enforcement, not a browser decoding/listening result.
});
