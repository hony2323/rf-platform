import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/ws": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
      "/auth": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/agents": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/me": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
