import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: { brand: { DEFAULT: "#1f4e79", dark: "#143352" } },
    },
  },
  plugins: [],
};

export default config;
