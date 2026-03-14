module.exports = {
  content: [
    "./templates/**/*.html",
    "!./templates/request.html",
    "!./templates/request_login.html",
    "!./templates/request/**",
    "./static/js/admin/**/*.js",
  ],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        apple: {
          bgLight: "#F2F2F7",
          bgDark: "#000000",
          cardLight: "#FFFFFF",
          cardDark: "#1C1C1E",
          hoverDark: "#2C2C2E",
          borderLight: "#E5E5EA",
          borderDark: "#38383A",
        },
        brand: {
          50: "#eff6ff",
          100: "#dbeafe",
          200: "#bfdbfe",
          300: "#93c5fd",
          400: "#60a5fa",
          500: "#007AFF",
          600: "#0056b3",
          700: "#00438f",
          800: "#00326b",
          900: "#002247",
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
