import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // LaborAid brand (sampled from the laboraid.com crest):
        // navy field, gold droplets, red bend.
        brand: { DEFAULT: "#16295D", dark: "#0F1C42", light: "#2A3F7A" },
        gold: { DEFAULT: "#F8C431", dark: "#D9A81E" },
        accent: { DEFAULT: "#C50000", dark: "#9E0000" },
      },
    },
  },
  plugins: [],
};

export default config;
