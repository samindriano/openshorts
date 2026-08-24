// The backend exposes this same object from /api/config. These values are a
// safe offline fallback for the first render before AuthContext has loaded.
export const DEFAULT_SUBTITLE_OPTIONS = Object.freeze({
  position: 'bottom',
  fontSize: 36,
  fontName: 'Anton',
  fontColor: '#FFFFFF',
  highlightColor: '#FFE500',
  borderColor: '#000000',
  borderWidth: 3,
  bgColor: '#000000',
  bgOpacity: 0,
  style: 'karaoke',
  animation: 'pop',
  effect: 'pop',
  baseOpacity: 1,
  uppercase: true,
});

const VALID = {
  position: new Set(['top', 'middle', 'bottom']),
  style: new Set(['classic', 'karaoke']),
  animation: new Set(['none', 'pop', 'word-highlight', 'karaoke']),
  effect: new Set(['none', 'glow', 'pop', 'box']),
};

const numberOr = (value, fallback, min, max, integer = false) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  const clamped = Math.max(min, Math.min(max, n));
  return integer ? Math.round(clamped) : clamped;
};

export function normalizeSubtitleConfig(config = {}, defaults = DEFAULT_SUBTITLE_OPTIONS) {
  const source = { ...defaults, ...(config || {}) };
  const out = {
    ...DEFAULT_SUBTITLE_OPTIONS,
    ...source,
    position: VALID.position.has(source.position) ? source.position : defaults.position,
    style: VALID.style.has(source.style) ? source.style : defaults.style,
    animation: VALID.animation.has(source.animation) ? source.animation : defaults.animation,
    effect: VALID.effect.has(source.effect) ? source.effect : defaults.effect,
    fontSize: numberOr(source.fontSize, defaults.fontSize, 10, 200, true),
    borderWidth: numberOr(source.borderWidth, defaults.borderWidth, 0, 10, true),
    bgOpacity: numberOr(source.bgOpacity, defaults.bgOpacity, 0, 1),
    baseOpacity: numberOr(source.baseOpacity, defaults.baseOpacity, 0.05, 1),
    uppercase: Boolean(source.uppercase),
  };
  if (out.style === 'classic') {
    // Classic is a real uniform-color mode. Animation is an explicit style
    // choice, not a transport-side promotion to karaoke.
    out.animation = 'none';
    out.effect = 'none';
    out.baseOpacity = 1;
    out.uppercase = false;
  }
  return out;
}

export function serializeSubtitleRequest(options = {}, extras = {}, includeCaptions = true) {
  const config = normalizeSubtitleConfig(options, options.defaults || DEFAULT_SUBTITLE_OPTIONS);
  return {
    ...extras,
    position: config.position,
    font_size: config.fontSize,
    font_name: config.fontName,
    font_color: config.fontColor,
    highlight_color: config.highlightColor,
    border_color: config.borderColor,
    border_width: config.borderWidth,
    bg_color: config.bgColor,
    bg_opacity: config.bgOpacity,
    style: config.style,
    animation: config.animation,
    effect: config.effect,
    base_opacity: config.baseOpacity,
    uppercase: config.uppercase,
    // Bulk deliberately sends null: each destination gets its own server
    // transcript/timing and must never inherit the source modal's words.
    words: includeCaptions && Array.isArray(options.captions)
      ? options.captions
      : null,
  };
}

export function toRemotionSubtitleConfig(options = {}, defaults = DEFAULT_SUBTITLE_OPTIONS) {
  const config = normalizeSubtitleConfig(options, defaults);
  return {
    captions: Array.isArray(options.captions) ? options.captions : [],
    position: config.position,
    style: {
      mode: config.style,
      fontFamily: config.fontName,
      fontSize: config.fontSize * 2,
      fontColor: config.fontColor,
      highlightColor: config.highlightColor,
      borderColor: config.borderColor,
      borderWidth: config.borderWidth * 1.5,
      bgColor: config.bgColor,
      bgOpacity: config.bgOpacity,
      animation: config.animation,
      baseOpacity: config.style === 'karaoke' ? config.baseOpacity : 1,
      uppercase: config.style === 'karaoke' ? config.uppercase : false,
    },
  };
}

// Kept as an identity adapter so older callers do not mutate request bodies.
// Previous versions silently rewrote classic+different-highlight to karaoke,
// which made network payloads differ from the selected UI recipe.
export function normalizeSubtitleRequestOptions(_path, options = {}) {
  return options;
}
