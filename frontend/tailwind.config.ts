import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#16202e", soft: "#4a5568", faint: "#718096" },
        brand: { DEFAULT: "#1f4e79", dark: "#163a5a", light: "#e8f0f8" },
      },
      fontFamily: { sans: ["Inter", "system-ui", "Segoe UI", "Roboto", "sans-serif"] },
    },
  },
  plugins: [],
};

export default config;
