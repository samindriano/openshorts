import React, { useState, useEffect } from 'react';
import { Key, Eye, EyeOff, Check } from 'lucide-react';

export default function KeyInput({
    onKeySet,
    savedKey,
    title = 'Gemini API Key',
    placeholder = 'AIzaSy...',
    helpHref = 'https://aistudio.google.com/app/apikey',
    helpText = 'Get your free Gemini API Key here →',
}) {
    const [key, setKey] = useState(savedKey || '');
    const [isVisible, setIsVisible] = useState(false);
    const [isSaved, setIsSaved] = useState(!!savedKey);

    useEffect(() => {
        setKey(savedKey || '');
        setIsSaved(!!savedKey);
    }, [savedKey]);

    const handleSave = () => {
        const next = key.trim();
        if (next.length > 0) {
            onKeySet(next);
            setIsSaved(true);
        }
    };

    return (
        <div className="card p-4 sm:p-6 mb-8 animate-fade">
            <div className="flex items-center gap-3 mb-4">
                <div className="p-2 bg-paper3 rounded-input text-brass">
                    <Key size={18} />
                </div>
                <h2 className="font-display lowercase text-lg text-ink">{title}</h2>
            </div>

            <div className="flex flex-col sm:flex-row gap-3">
                <div className="relative sm:flex-1">
                    <input
                        type={isVisible ? "text" : "password"}
                        value={key}
                        onChange={(e) => {
                            setKey(e.target.value);
                            setIsSaved(false);
                        }}
                        placeholder={placeholder}
                        className="input-field pr-12 font-mono"
                    />
                    <button
                        type="button"
                        onClick={() => setIsVisible(!isVisible)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-ink transition-colors"
                        aria-label={isVisible ? `Hide ${title}` : `Show ${title}`}
                    >
                        {isVisible ? <EyeOff size={18} /> : <Eye size={18} />}
                    </button>
                </div>
                <button
                    type="button"
                    onClick={handleSave}
                    disabled={!key.trim() || isSaved}
                    className={isSaved ? 'badge-ok px-4 cursor-default' : 'btn-primary'}
                >
                    {isSaved ? <><Check size={14} /> Ready</> : 'Set Key'}
                </button>
            </div>
            <p className="mt-3 text-xs text-muted">
                Your key is stored locally in your browser for convenience.
                {helpHref && (
                    <>
                        <br />
                        <a
                            href={helpHref}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-brass hover:underline mt-1 inline-block"
                        >
                            {helpText}
                        </a>
                    </>
                )}
            </p>
        </div>
    );
}
