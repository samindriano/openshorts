/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Light neutral palette — values mirror tokens.css (kept literal so
        // Tailwind alpha modifiers like bg-brass/10 compile)
        paper: "rgb(250 250 248 / <alpha-value>)",
        paper2: "rgb(241 241 239 / <alpha-value>)",
        paper3: "rgb(236 236 234 / <alpha-value>)",
        card: "rgb(255 255 255 / <alpha-value>)",
        ink: "rgb(31 31 29 / <alpha-value>)",
        ink2: "rgb(69 69 65 / <alpha-value>)",
        muted: "rgb(107 107 103 / <alpha-value>)",
        brass: "rgb(232 111 42 / <alpha-value>)",
        brassink: "rgb(45 22 10 / <alpha-value>)",
        coral: "rgb(200 90 33 / <alpha-value>)",
        ok: "rgb(46 125 82 / <alpha-value>)",
        warn: "rgb(154 93 9 / <alpha-value>)",
        danger: "rgb(184 60 60 / <alpha-value>)",
        info: "rgb(63 111 159 / <alpha-value>)",
        // legacy aliases so untouched files degrade gracefully
        background: "rgb(250 250 248 / <alpha-value>)",
        surface: "rgb(241 241 239 / <alpha-value>)",
        primary: "rgb(232 111 42 / <alpha-value>)",
        accent: "rgb(200 90 33 / <alpha-value>)",
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
