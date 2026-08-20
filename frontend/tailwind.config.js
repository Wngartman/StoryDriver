/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "rgb(var(--sd-color-ink) / <alpha-value>)",
        panel: "rgb(var(--sd-color-panel) / <alpha-value>)",
        panelSoft: "rgb(var(--sd-color-panel-soft) / <alpha-value>)",
        line: "rgb(var(--sd-color-line) / <alpha-value>)",
        story: "rgb(var(--sd-color-story) / <alpha-value>)",
        muted: "rgb(var(--sd-color-muted) / <alpha-value>)",
        moss: "rgb(var(--sd-color-moss) / <alpha-value>)",
        ember: "rgb(var(--sd-color-ember) / <alpha-value>)",
        tide: "rgb(var(--sd-color-tide) / <alpha-value>)",
      },
      boxShadow: {
        glow: "var(--sd-shadow-glow)",
        focus: "var(--sd-shadow-focus)",
      },
      fontFamily: {
        sans: [
          "var(--sd-font-sans)",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
        story: [
          "var(--sd-font-story)",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
