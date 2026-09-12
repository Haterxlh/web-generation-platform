import { fileURLToPath, URL } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      // 路径别名：@/xxx -> src/xxx
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 开发环境把 /api 请求原样转发到 FastAPI 后端（backend-uv-fastapi，默认 8000），避免跨域
      // 后端路由统一带 /api 前缀（app/main.py 中 include_router(..., prefix="/api")），因此不做 rewrite
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
