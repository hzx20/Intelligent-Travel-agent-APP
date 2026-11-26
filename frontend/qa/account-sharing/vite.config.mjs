import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  root: fileURLToPath(new URL('../../', import.meta.url)),
  base: '/travel/',
  envDir: false,
  plugins: [vue()],
  server: {
    host: '127.0.0.1', port: 5264, strictPort: true,
    proxy: { '/travel/api': { target: 'http://127.0.0.1:18963', changeOrigin: true,
      rewrite: path => path.replace(/^\/travel/, '') } },
  },
})
