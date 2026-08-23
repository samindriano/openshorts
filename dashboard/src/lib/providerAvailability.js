export function getProviderAvailability({
    billingEnabled = false,
    browserGeminiKey = '',
    browserOpenaiKey = '',
    serverGeminiConfigured = false,
    serverOpenaiConfigured = false,
} = {}) {
    const geminiConfigured = Boolean(browserGeminiKey)
        || (!billingEnabled && Boolean(serverGeminiConfigured));
    const openaiConfigured = Boolean(browserOpenaiKey)
        || (!billingEnabled && Boolean(serverOpenaiConfigured));

    return {
        geminiConfigured,
        openaiConfigured,
        keysMissing: !billingEnabled && !geminiConfigured && !openaiConfigured,
    };
}
