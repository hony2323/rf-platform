import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

function isHtmlNavigationRequest(headers: Record<string, string | string[] | undefined>): boolean {
  const accept = headers.accept;
  if (typeof accept !== "string") return false;
  return accept.includes("text/html");
}

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
        bypass(req) {
          if (isHtmlNavigationRequest(req.headers)) {
            return "/index.html";
          }
          return undefined;
        },
      },
      "/me": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
