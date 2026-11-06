import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// 默认部署在域名根路径；服务器 80 端口已有别的站点时，
// 用 `npm run build:travel`（读取 .env.travel）构建到 /travel/ 子路径，
// 不把某个具体部署环境写死在代码里。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    base: env.VITE_BASE || '/',
    plugins: [vue()],
    server: {
      port: 5173,
      proxy: {
        '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      },
    },
  }
})
