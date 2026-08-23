import test from 'node:test';
import assert from 'node:assert/strict';
import { getProviderAvailability } from './providerAvailability.js';

test('self-host server availability satisfies the provider gate', () => {
    assert.deepEqual(
        getProviderAvailability({ serverGeminiConfigured: true }),
        { geminiConfigured: true, openaiConfigured: false, keysMissing: false },
    );
    assert.deepEqual(
        getProviderAvailability({ serverOpenaiConfigured: true }),
        { geminiConfigured: false, openaiConfigured: true, keysMissing: false },
    );
});

test('browser keys and server flags combine without exposing key values', () => {
    assert.deepEqual(
        getProviderAvailability({
            browserGeminiKey: 'browser-gemini',
            serverOpenaiConfigured: true,
        }),
        { geminiConfigured: true, openaiConfigured: true, keysMissing: false },
    );
});

test('hosted mode ignores self-host server flags', () => {
    assert.deepEqual(
        getProviderAvailability({
            billingEnabled: true,
            serverGeminiConfigured: true,
            serverOpenaiConfigured: true,
        }),
        { geminiConfigured: false, openaiConfigured: false, keysMissing: false },
    );
});

test('no browser key or self-host server flag keeps the gate closed', () => {
    assert.deepEqual(
        getProviderAvailability(),
        { geminiConfigured: false, openaiConfigured: false, keysMissing: true },
    );
});
