/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Soft sage-slate palette — values mirror tokens.css (kept literal so
        // Tailwind alpha modifiers like bg-brass/10 compile)
        paper: "rgb(237 241 239 / <alpha-value>)",
        paper2: "rgb(228 233 231 / <alpha-value>)",
        paper3: "rgb(219 226 223 / <alpha-value>)",
        card: "rgb(247 249 248 / <alpha-value>)",
        ink: "rgb(32 36 33 / <alpha-value>)",
        ink2: "rgb(62 73 69 / <alpha-value>)",
        muted: "rgb(95 106 101 / <alpha-value>)",
        brass: "rgb(220 108 50 / <alpha-value>)",
        brassink: "rgb(45 22 10 / <alpha-value>)",
        coral: "rgb(184 90 39 / <alpha-value>)",
        ok: "rgb(46 125 82 / <alpha-value>)",
        warn: "rgb(154 93 9 / <alpha-value>)",
        danger: "rgb(184 60 60 / <alpha-value>)",
        info: "rgb(82 124 120 / <alpha-value>)",
        // legacy aliases so untouched files degrade gracefully
        background: "rgb(237 241 239 / <alpha-value>)",
        surface: "rgb(228 233 231 / <alpha-value>)",
        primary: "rgb(220 108 50 / <alpha-value>)",
        accent: "rgb(184 90 39 / <alpha-value>)",
      },
      fontFamily: {
        display: "var(--font-display)",
        body: "var(--font-body)",
        sans: "var(--font-body)",
        serif: "var(--font-display)",
        mono: "var(--font-mono)",
      },
      borderColor: {
        rule: "var(--color-rule)",
        rule2: "var(--color-rule-2)",
      },
      borderRadius: {
        card: "var(--radius-card)",
        input: "var(--radius-input)",
      },
      fontSize: {
        micro: ["10.5px", { letterSpacing: "0.10em" }],
      },
      transitionTimingFunction: {
        out: "var(--ease-out)",
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade': 'fadeIn 0.4s var(--ease-out)',
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
