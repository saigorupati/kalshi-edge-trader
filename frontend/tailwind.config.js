/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Bloomberg terminal palette
        bg: {
          primary:   '#0a0a0f',
          secondary: '#0f0f18',
          card:      '#13131f',
          hover:     '#1a1a2e',
          border:    '#1e1e30',
        },
        accent: {
          cyan:   '#00d4ff',
          green:  '#00ff88',
          red:    '#ff3366',
          yellow: '#ffcc00',
          purple: '#9b59ff',
          orange: '#ff8c00',
        },
        text: {
          primary:   '#e8e8f0',
          secondary: '#8888aa',
          muted:     '#555577',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in':    'fadeIn 0.3s ease-in',
        'slide-up':   'slideUp 0.2s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%':   { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%':   { transform: 'translateY(8px)', opacity: '0' },
          '100%': { transform: 'translateY(0)',   opacity: '1' },
        },
      },
    },
  },
  plugins: [],
};
