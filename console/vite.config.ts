import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  base: process.env.VITE_BASE_PATH || '/',
  plugins: [react(), tailwindcss()],
  resolve: { alias: {
    '@': fileURLToPath(new URL('./src', import.meta.url)),
    '@console': fileURLToPath(new URL('./src', import.meta.url)),
  } },
  server: {
    host: process.env.ANP_DEV_HOST || '127.0.0.1', port: Number(process.env.ANP_UI_PORT || 5173), strictPort: true,
    fs: { allow: ['..'] },
    proxy: { '/api': process.env.ANP_API_TARGET || 'http://127.0.0.1:8180' },
    hmr: process.env.VITE_PUBLIC_PORT ? {clientPort:Number(process.env.VITE_PUBLIC_PORT)} : undefined,
  },
  build: { sourcemap: true },
})
