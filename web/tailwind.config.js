/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // RoadsEye — asphalt & lane-marking theme
        asphalt: {
          DEFAULT: '#0b0b0d',    // page background — blacktop
          panel:   '#151517',    // raised panel
          deep:    '#101012',    // inset zone (charts, canvas)
        },
        lane: {
          DEFAULT: '#FFD100',    // road-marking yellow
          deep:    '#c9a600',
          dim:     '#5c4f00',
          wash:    'rgba(255, 209, 0, 0.08)',
        },
        chalk:   '#EDEDEA',      // near-white text
        pip:     '#8f8f8a',      // muted text
        go:      '#22C55E',
        stop:    '#EF4444',
        warn:    '#FFA500',
      },
      fontFamily: {
        code: ['"Fira Code"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
        sans: ['"Fira Sans"', 'system-ui', 'sans-serif'],
      },
      backgroundImage: {
        'lane-h': 'repeating-linear-gradient(90deg, #FFD100 0 26px, transparent 26px 48px)',
        'lane-v': 'repeating-linear-gradient(0deg, #FFD100 0 26px, transparent 26px 48px)',
        'grain':
          'linear-gradient(rgba(255,209,0,0.025) 1px, transparent 1px),'
        + 'linear-gradient(90deg, rgba(255,209,0,0.025) 1px, transparent 1px)',
      },
      backgroundSize: {
        grain: '40px 40px',
      },
      boxShadow: {
        glowlane: '0 0 14px rgba(255,209,0,0.25)',
      },
    },
  },
  plugins: [],
}