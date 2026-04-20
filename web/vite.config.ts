import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
        bypass(req) {
          // Let Vite serve its own assets (JS, CSS, HTML, HMR)
          const url = req.url ?? "";
          if (
            url.startsWith("/@") ||
            url.startsWith("/src") ||
            url.startsWith("/node_modules") ||
            url === "/" ||
            url.endsWith(".html") ||
            url.endsWith(".tsx") ||
            url.endsWith(".ts") ||
            url.endsWith(".css") ||
            url.endsWith(".js") ||
            url.endsWith(".jsx") ||
            url.endsWith(".map")
          ) {
            return url;
          }
          return null;
        },
      },
    },
  },
});
