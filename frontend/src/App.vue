<script setup>
import { ref } from 'vue'
import { useAuth } from './store/auth'

const { user, login, register, logout } = useAuth()
const showLogin = ref(false)
const mode = ref('login') // login | register
const username = ref('')
const password = ref('')
const nickname = ref('')
const err = ref('')
const busy = ref(false)

async function submit() {
  err.value = ''
  busy.value = true
  try {
    if (mode.value === 'login') await login(username.value, password.value)
    else await register(username.value, nickname.value || username.value, password.value)
    showLogin.value = false
  } catch (e) {
    err.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <nav class="nav">
    <router-link to="/" class="logo">🧭 旅行规划平台</router-link>
    <div class="links">
      <router-link to="/">首页</router-link>
      <router-link to="/spots">旅游景点</router-link>
    </div>
    <span class="user">
      <template v-if="user">
        你好，{{ user.nickname }} · <span class="lk" @click="logout">退出</span>
      </template>
      <template v-else>
        游客 · <span class="lk" @click="showLogin = true; mode = 'login'">登录</span> / <span class="lk" @click="showLogin = true; mode = 'register'">注册</span>
      </template>
    </span>
  </nav>

  <router-view />

  <!-- 登录/注册弹窗 -->
  <div v-if="showLogin" class="modal-mask" @click.self="showLogin = false">
    <div class="modal">
      <div class="tabs">
        <span :class="{ on: mode === 'login' }" @click="mode = 'login'">登录</span>
        <span :class="{ on: mode === 'register' }" @click="mode = 'register'">注册</span>
      </div>
      <input v-model="username" placeholder="用户名（3 位以上，字母/数字/中文）" />
      <input v-model="nickname" v-if="mode === 'register'" placeholder="昵称（可留空）" />
      <input v-model="password" type="password" placeholder="密码（6 位以上）" @keyup.enter="submit" />
      <p v-if="err" class="err">{{ err }}</p>
      <button class="btn-main" :disabled="busy" @click="submit">
        {{ busy ? '请稍候…' : (mode === 'login' ? '登录' : '注册并登录') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.modal-mask {
  position: fixed; inset: 0; background: rgba(0,0,0,.4);
  display: flex; align-items: center; justify-content: center; z-index: 50;
}
.modal {
  width: 320px; background: #fff; border-radius: 14px; padding: 22px;
  display: flex; flex-direction: column; gap: 12px;
}
.tabs { display: flex; gap: 6px; margin-bottom: 4px; }
.tabs span {
  flex: 1; text-align: center; padding: 8px 0; border-radius: 8px;
  background: #f2f1ed; color: #666; cursor: pointer; font-size: 13.5px;
}
.tabs span.on { background: var(--green); color: #fff; }
.modal input {
  border: 1.5px solid #ccc; border-radius: 10px; padding: 10px 12px; font-size: 13.5px;
}
.modal input:focus { outline: none; border-color: var(--green); }
.err { color: #c0392b; font-size: 12.5px; }
.btn-main {
  background: var(--green); color: #fff; border: none;
  border-radius: 10px; padding: 11px 0; font-size: 14.5px;
}
.btn-main:disabled { opacity: .6; }
</style>
