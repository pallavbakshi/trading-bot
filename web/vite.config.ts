import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  root: ".",
  plugins: [tailwindcss()],
  define: {
    "import.meta.env.OPENROUTER_API_KEY": JSON.stringify(process.env.OPENROUTER_API_KEY || ""),
  },
  server: {
    port: 3000,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
