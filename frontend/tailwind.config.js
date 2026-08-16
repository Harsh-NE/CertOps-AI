/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0f14",
        panel: "#121821",
        panel2: "#1a2230",
        border: "#243040",
        accent: "#4f9dff",
        accent2: "#7cf5c0",
        warn: "#ffb454",
        danger: "#ff5c7a",
        muted: "#7c8ba1",
      },
    },
  },
  plugins: [],
}
