import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发期代理：前端 5173 → 后端 8000，浏览器同源无跨域
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
