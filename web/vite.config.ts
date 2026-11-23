import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

// En desarrollo, /api se reenvía a la API local (make api).
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
