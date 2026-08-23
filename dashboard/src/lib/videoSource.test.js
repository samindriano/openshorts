import test from 'node:test';
import assert from 'node:assert/strict';
import {
    cacheBustVideoUrl,
    filenameFromVideoUrl,
    selectPlaybackUrl,
} from './videoSource.js';

test('versioned server URLs cannot reuse a cached video response', () => {
    assert.equal(
        cacheBustVideoUrl('/videos/job/clip.mp4', '178748123'),
        '/videos/job/clip.mp4?rev=178748123',
    );
    assert.equal(
        cacheBustVideoUrl('/videos/job/clip.mp4?download=1', 'next'),
        '/videos/job/clip.mp4?download=1&rev=next',
    );
});

test('server filenames ignore revision query parameters', () => {
    assert.equal(
        filenameFromVideoUrl('/videos/job/subtitled_2_clip.mp4?rev=2'),
        'subtitled_2_clip.mp4',
    );
});

test('a stale durable URL never wins after a server edit or browser preview', () => {
    assert.equal(
        selectPlaybackUrl({
            durableSrc: 'https://r2.example/old.mp4',
            durableFailed: false,
            currentVideoUrl: '/videos/job/new.mp4?rev=2',
        }),
        'https://r2.example/old.mp4',
    );
    assert.equal(
        selectPlaybackUrl({
            durableSrc: 'https://r2.example/old.mp4',
            durableFailed: true,
            currentVideoUrl: '/videos/job/new.mp4?rev=2',
        }),
        '/videos/job/new.mp4?rev=2',
    );
    assert.equal(
        selectPlaybackUrl({
            durableSrc: 'https://r2.example/old.mp4',
            durableFailed: false,
            currentVideoUrl: 'blob:http://localhost/preview',
        }),
        'blob:http://localhost/preview',
    );
});
