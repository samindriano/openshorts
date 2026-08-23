export function cacheBustVideoUrl(url, revision) {
    if (!url || !revision) return url;
    const separator = String(url).includes('?') ? '&' : '?';
    return `${url}${separator}rev=${encodeURIComponent(revision)}`;
}

export function filenameFromVideoUrl(url) {
    return String(url || '').split('?')[0].split('/').pop() || '';
}

export function selectPlaybackUrl({ durableSrc, durableFailed, currentVideoUrl }) {
    if (durableSrc && !durableFailed && !String(currentVideoUrl || '').startsWith('blob:')) {
        return durableSrc;
    }
    return currentVideoUrl;
}
