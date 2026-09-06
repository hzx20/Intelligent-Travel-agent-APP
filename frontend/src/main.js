import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { useAuth } from './store/auth'
import './assets/main.css'

const app = createApp(App)
app.use(router)

// 启动时尝试恢复登录态（token 未过期则直接登录）
useAuth().restore().finally(() => app.mount('#app'))
