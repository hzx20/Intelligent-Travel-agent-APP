import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Run the complete app on a separate origin with no credentials or API proxy.
export default defineConfig({
  root: fileURLToPath(new URL('../../', import.meta.url)),
  base: '/travel/',
  envDir: false,
  plugins: [vue(), {
    name: 'isolated-draft-guard-qa',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith('/api/')) return next()
        res.writeHead(503, { 'Content-Type': 'application/json; charset=utf-8' })
        res.end(JSON.stringify({ detail: '隔离草稿验收不连接后端服务' }))
      })
    },
  }],
  server: { host: '127.0.0.1', port: 5263, strictPort: true },
})
