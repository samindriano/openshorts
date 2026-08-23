import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeSubtitleRequestOptions } from './subtitleRequest.js';

test('classic preview with a distinct active-word color uses durable karaoke renderer', () => {
  const options = {
    method: 'POST',
    body: JSON.stringify({
      style: 'classic',
      animation: 'none',
      font_color: '#FFFFFF',
      highlight_color: '#FFD700',
      font_size: 20,
    }),
  };
  const normalized = normalizeSubtitleRequestOptions('/api/subtitle', options);
  const payload = JSON.parse(normalized.body);
  assert.equal(payload.style, 'karaoke');
  assert.equal(payload.effect, 'none');
  assert.equal(payload.font_size, 20);
});

test('classic uniform-color request remains classic', () => {
  const options = {
    body: JSON.stringify({
      style: 'classic',
      animation: 'none',
      font_color: '#FFFFFF',
      highlight_color: '#FFFFFF',
    }),
  };
  assert.strictEqual(normalizeSubtitleRequestOptions('/api/subtitle', options), options);
});

test('existing karaoke request is not rewritten', () => {
  const options = { body: JSON.stringify({ style: 'karaoke', highlight_color: '#FFD700' }) };
  assert.strictEqual(normalizeSubtitleRequestOptions('/api/subtitle', options), options);
});

test('unrelated requests and non-JSON bodies are untouched', () => {
  const options = { body: JSON.stringify({ style: 'classic', font_color: '#fff', highlight_color: '#000' }) };
  assert.strictEqual(normalizeSubtitleRequestOptions('/api/process', options), options);
  const form = { body: { form: true } };
  assert.strictEqual(normalizeSubtitleRequestOptions('/api/subtitle', form), form);
});
