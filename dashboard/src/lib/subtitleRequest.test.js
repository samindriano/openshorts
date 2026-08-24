import test from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeSubtitleRequestOptions,
  serializeSubtitleRequest,
  toRemotionSubtitleConfig,
} from './subtitleRequest.js';

test('request normalization does not rewrite the selected classic mode', () => {
  const options = {
    method: 'POST',
    body: JSON.stringify({
      style: 'classic',
      animation: 'none',
      font_color: '#FFFFFF',
      highlight_color: '#FFD700',
    }),
  };
  assert.strictEqual(normalizeSubtitleRequestOptions('/api/subtitle', options), options);
});

test('single serializer carries the exact edited words and source file', () => {
  const payload = serializeSubtitleRequest({
    style: 'classic',
    animation: 'none',
    fontSize: 28,
    captions: [{ text: 'edited', startMs: 100, endMs: 500 }],
  }, { job_id: 'job-1', clip_index: 2, input_filename: 'subtitled_1_clip.mp4' });
  assert.equal(payload.style, 'classic');
  assert.equal(payload.animation, 'none');
  assert.equal(payload.input_filename, 'subtitled_1_clip.mp4');
  assert.deepEqual(payload.words, [{ text: 'edited', startMs: 100, endMs: 500 }]);
});

test('bulk serializer never leaks source captions to another clip', () => {
  const payload = serializeSubtitleRequest({
    style: 'karaoke',
    captions: [{ text: 'source-only', startMs: 0, endMs: 400 }],
  }, { job_id: 'job-1', clip_index: 4, input_filename: 'clip-5.mp4' }, false);
  assert.equal(payload.input_filename, 'clip-5.mp4');
  assert.equal(payload.words, null);
});

test('classic Remotion preview has an explicit uniform mode', () => {
  const preview = toRemotionSubtitleConfig({
    style: 'classic',
    animation: 'none',
    captions: [{ text: 'same', startMs: 0, endMs: 400 }],
  });
  assert.equal(preview.style.mode, 'classic');
  assert.equal(preview.style.animation, 'none');
  assert.deepEqual(preview.captions, [{ text: 'same', startMs: 0, endMs: 400 }]);
});

test('karaoke Remotion preview carries canonical casing and inactive-word opacity', () => {
  const preview = toRemotionSubtitleConfig({
    style: 'karaoke',
    animation: 'pop',
    baseOpacity: 0.6,
    uppercase: true,
  });
  assert.equal(preview.style.baseOpacity, 0.6);
  assert.equal(preview.style.uppercase, true);
});
