import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// El frontend solo habla con /api; en dev se proxya al backend FastAPI.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
