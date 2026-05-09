/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        spark: {
          bg: "#0b0d10",
          panel: "#14181d",
          border: "#1f242b",
          accent: "#f59e0b",
          muted: "#7d8590",
          text: "#e6edf3",
          danger: "#f85149",
          good: "#3fb950",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
