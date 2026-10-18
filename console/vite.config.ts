import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";
import { reviewBridge } from "../apps/deep_dub/dev/review-bridge";

export default defineConfig(({ mode }) => ({
  optimizeDeps:
    mode === "deep-dub-review" ? { entries: ["deep-dub.html"] } : undefined,
  base: process.env.VITE_BASE_PATH || "/",
  cacheDir: process.env.ANP_VITE_CACHE_DIR || undefined,
  plugins: [
    react(),
    tailwindcss(),
    ...(mode === "deep-dub-review" ? [reviewBridge()] : []),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "@console": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: process.env.ANP_DEV_HOST || "127.0.0.1",
    port: Number(process.env.ANP_UI_PORT || 5173),
    strictPort: true,
    fs: { allow: [".."] },
    proxy: {
      "/api": process.env.ANP_API_TARGET || "http://127.0.0.1:8180",
      ...(process.env.ANP_SUPABASE_DEV_GATEWAY ? {"/supabase":{target:process.env.ANP_SUPABASE_DEV_GATEWAY,changeOrigin:true}} : {}),
    },
    hmr: process.env.VITE_PUBLIC_PORT
      ? { clientPort: Number(process.env.VITE_PUBLIC_PORT) }
      : undefined,
  },
  build: {
    sourcemap: true,
    rollupOptions: {
      ...(mode === "deep-dub-review" ? { input: fileURLToPath(new URL('./deep-dub.html', import.meta.url)) } : {}),
    },
  },
}));
