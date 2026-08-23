import React from 'react';
import { Check, KeyRound, Sparkles } from 'lucide-react';
import Modal from './ui/Modal';

const PROVIDERS = [
  {
    id: 'gemini',
    eyebrow: 'GOOGLE',
    title: 'Gemini 3.1 Flash-Lite',
    description: 'Use your Gemini API key for transcript scoring and clip selection.',
  },
  {
    id: 'openai',
    eyebrow: 'OPENAI',
    title: 'GPT-5.6 Luna',
    description: 'Use your OpenAI API balance for the same scoring and clip-selection pipeline.',
  },
];

export default function AIProviderModal({
  isOpen,
  onClose,
  onChoose,
  onOpenSettings,
  geminiConfigured = false,
  openaiConfigured = false,
  geminiServerConfigured = false,
  openaiServerConfigured = false,
}) {
  const ready = { gemini: geminiConfigured, openai: openaiConfigured };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      eyebrow="CLIP ANALYSIS"
      title="Choose AI for this scan"
      size="md"
    >
      <div className="space-y-4">
        <p className="text-sm text-muted leading-relaxed">
          Both options use the same transcript windows, clip rules, timestamp snapping,
          reframing, subtitles, and renderer. Only the model that scores and selects the
          moments changes.
        </p>

        <div className="grid gap-3 sm:grid-cols-2">
          {PROVIDERS.map((provider) => {
            const configured = ready[provider.id];
            return (
              <button
                key={provider.id}
                type="button"
                disabled={!configured}
                onClick={() => configured && onChoose(provider.id)}
                className={`text-left rounded-card border p-4 transition-colors ${
                  configured
                    ? 'border-rule2 hover:border-brass hover:bg-paper3'
                    : 'border-rule opacity-55 cursor-not-allowed'
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="readout mb-1">{provider.eyebrow}</div>
                    <div className="text-sm font-medium text-ink">{provider.title}</div>
                  </div>
                  <div className={`w-7 h-7 rounded-full flex items-center justify-center ${
                    configured ? 'bg-paper3 text-ok' : 'bg-paper3 text-muted'
                  }`}>
                    {configured ? <Check size={14} /> : <KeyRound size={14} />}
                  </div>
                </div>
                <p className="text-xs text-muted leading-relaxed mt-3">
                  {provider.description}
                </p>
                <div className={`mt-3 text-xs ${configured ? 'text-ok' : 'text-warn'}`}>
                  {configured
                    ? (provider.id === 'gemini' && geminiServerConfigured)
                      || (provider.id === 'openai' && openaiServerConfigured)
                      ? 'Configured on server'
                      : 'Browser API key ready'
                    : 'API key not configured'}
                </div>
              </button>
            );
          })}
        </div>

        {(!geminiConfigured || !openaiConfigured) && (
          <div className="flex items-center justify-between gap-3 rounded-input border border-rule bg-paper2 px-3 py-2.5">
            <div className="text-xs text-muted">
              Add both keys in Settings to switch freely per video.
            </div>
            <button
              type="button"
              onClick={onOpenSettings}
              className="btn-quiet py-1.5 px-3 text-xs shrink-0"
            >
              <Sparkles size={13} /> settings
            </button>
          </div>
        )}
      </div>
    </Modal>
  );
}
