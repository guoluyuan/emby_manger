module.exports = {
  content: [
    "./templates/request.html",
    "./templates/request_login.html",
    "./templates/request/**/*.html",
    "./static/js/request_app.js",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f0fdfa",
          100: "#ccfbf1",
          200: "#99f6e4",
          300: "#5eead4",
          400: "#2dd4bf",
          500: "#14b8a6",
          600: "#0d9488",
          700: "#0f766e",
          800: "#115e59",
          900: "#134e4a",
        },
        gray: {
          850: "#1f2937",
          900: "#111827",
          950: "#030712",
        },
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.4s ease-out",
      },
    },
  },
  safelist: [
    { pattern: /^(bg|text|border|ring|from|to|via)-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|blue|sky|indigo|violet|purple|fuchsia|pink|rose)(-[0-9]{2,3})?$/ },
  ],
  plugins: [],
};
