const normalizeColor = (value) => String(value || '').trim().toUpperCase();

export function normalizeSubtitleRequestOptions(path, options = {}) {
  if (path !== '/api/subtitle' || typeof options?.body !== 'string') return options;

  let payload;
  try {
    payload = JSON.parse(options.body);
  } catch (_) {
    return options;
  }

  const style = String(payload?.style || 'classic').toLowerCase();
  const animation = String(payload?.animation || 'none').toLowerCase();
  const fontColor = normalizeColor(payload?.font_color);
  const highlightColor = normalizeColor(payload?.highlight_color);

  // The Remotion preview always paints the active word with highlightColor.
  // A classic SRT burn cannot represent that active-word color, so promote
  // this request to the ASS/karaoke renderer whenever the preview visibly
  // distinguishes the highlight. This keeps Apply/Apply All WYSIWYG.
  const previewHasActiveWordHighlight =
    style === 'classic'
    && animation === 'none'
    && Boolean(highlightColor)
    && highlightColor !== fontColor;

  if (!previewHasActiveWordHighlight) return options;

  return {
    ...options,
    body: JSON.stringify({
      ...payload,
      style: 'karaoke',
      effect: payload?.effect || 'none',
    }),
  };
}
