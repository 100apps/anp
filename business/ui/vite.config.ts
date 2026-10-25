import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  resolve: {alias: {"@": fileURLToPath(new URL("./src", import.meta.url))}},
  plugins: [react(), tailwindcss(), {
    name: "anp-workspace",
    configureServer(server) {
      server.middlewares.use("/__anp_workspace", (_req, res) => {
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify({root: process.cwd(), project: "anp"}));
      });
    }
  }],
  base: "/workbench/",
  server: {
    proxy: {
      ...Object.fromEntries(["rustfs", "dbx", "langfuse", "litellm", "supabase", "grafana"].map(name => ["/" + name, {
        target: process.env.ANP_AGENT_URL || "http://127.0.0.1:20260", changeOrigin: false, ws: true
      }])),
      "/agent": {target: process.env.ANP_AGENT_URL || "http://127.0.0.1:20260",
                 changeOrigin: true, rewrite: path => path.replace(/^\/agent/, "")},
      "/api": {target: process.env.ANP_AGENT_URL || "http://127.0.0.1:20260", changeOrigin: true}
    }
  },
  build: {sourcemap: false}
});
