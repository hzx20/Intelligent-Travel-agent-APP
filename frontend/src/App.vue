<script setup>
import { reactive, ref, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useAuth } from './store/auth'
import TripEditDialog from './components/TripEditDialog.vue'

const { user, showLogin, loginMode, restore, restoreError, restorePending, externalSessionChange, login, register, logout, openLogin } = useAuth()
const form = reactive({ username: '', nickname: '', password: '' })
const err = ref('')
const busy = ref(false)
const menuOpen = ref(false) // 移动端汉堡菜单展开状态
const navRoot=ref(null),siteLinks=ref(null),menuButton=ref(null)

function closeMenu(restoreFocus=false) { menuOpen.value = false;if(restoreFocus===true)nextTick(()=>menuButton.value?.focus()) }
async function toggleMenu(){if(menuOpen.value){closeMenu(true);return}menuOpen.value=true;await nextTick();siteLinks.value?.querySelector('a')?.focus()}
function outsideMenu(e){if(menuOpen.value&&!navRoot.value?.contains(e.target))closeMenu()}
function leaveMenu(e){if(menuOpen.value&&e.relatedTarget&&!navRoot.value?.contains(e.relatedTarget))closeMenu()}
onMounted(()=>document.addEventListener('pointerdown',outsideMenu))
onBeforeUnmount(()=>document.removeEventListener('pointerdown',outsideMenu))
function closeLogin() {
  if (busy.value) return
  showLogin.value = false; form.password = ''; err.value = ''
}
function changeLoginMode(mode) {
  if (busy.value) return
  loginMode.value = mode; err.value = ''
}

async function submit() {
  if (busy.value) return
  err.value = ''
  busy.value = true
  try {
    if (loginMode.value === 'login') await login(form.username, form.password)
    else await register(form.username, form.password, form.nickname || form.username)
    showLogin.value = false; form.password = ''
  } catch (e) {
    err.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div :inert="busy">
  <nav ref="navRoot" class="nav" aria-label="站点导航" @keydown.esc.prevent="closeMenu(true)" @focusout="leaveMenu">
    <router-link to="/" class="logo" @click="closeMenu">🧭 旅行规划平台</router-link>
    <div id="site-links" ref="siteLinks" class="links" :class="{ open: menuOpen }">
      <router-link to="/" @click="closeMenu">我的旅行</router-link>
      <router-link to="/spots" @click="closeMenu">探索地点</router-link>
      <router-link to="/inspiration" @click="closeMenu">旅行灵感</router-link>
      <router-link to="/guides" @click="closeMenu">旅游攻略</router-link>
      <router-link to="/travel-data" @click="closeMenu">天气与航班</router-link>
      <router-link v-if="user" to="/me" @click="closeMenu">个人中心</router-link>
      <router-link v-if="user?.is_admin" to="/admin" @click="closeMenu">管理后台</router-link>
    </div>
    <!-- 核心入口做成实心按钮，且放在 .links 之外：
         小屏时 .links 整体收起，这一颗仍然常驻，核心功能不会跟着消失 -->
    <router-link to="/plan" class="nav-cta" @click="closeMenu">AI 规划</router-link>
    <span class="user">
      <template v-if="user">
        <span class="u-hello">你好，{{ user.nickname }} · </span><button type="button" class="lk" @click="logout">退出</button>
      </template>
      <template v-else>
        <span class="u-hello">游客 · </span><button type="button" class="lk" @click="openLogin('login')">登录</button> /
        <button type="button" class="lk" @click="openLogin('register')">注册</button>
      </template>
    </span>
    <button ref="menuButton" type="button" class="nav-toggle" :aria-label="menuOpen ? '收起导航菜单' : '打开导航菜单'" aria-controls="site-links" :aria-expanded="menuOpen" @click="toggleMenu">☰</button>
  </nav>

  <div v-if="externalSessionChange" class="auth-warning" role="status">
    <span>其他页面已更改登录状态。本页仍保留原账号内容和草稿，账号请求已暂停；已发出的操作可能仍会完成。请处理未保存内容后同步账号。</span>
    <button type="button" :disabled="restorePending" @click="restore">{{ restorePending ? '正在同步…' : '同步账号' }}</button>
  </div>
  <div v-if="restoreError" class="auth-warning" role="status">
    <span>{{ restoreError }}</span>
    <button type="button" :disabled="restorePending" @click="restore">{{ restorePending ? '正在重试…' : '重试' }}</button>
    <button v-if="!user" type="button" @click="openLogin('login')">登录</button>
  </div>
  <router-view />

  <nav class="mobile-nav" aria-label="主要导航">
    <router-link to="/" @click="closeMenu"><span>⌂</span>旅行</router-link>
    <router-link to="/spots" @click="closeMenu"><span>⌕</span>探索</router-link>
    <router-link to="/trips/new" class="mobile-new" @click="closeMenu"><span>＋</span>创建</router-link>
    <router-link to="/guides" @click="closeMenu"><span>▤</span>攻略</router-link>
    <router-link to="/me" @click="closeMenu"><span>◉</span>我的</router-link>
  </nav>
  </div>

  <!-- 登录/注册弹窗（状态在 store，任何页面可唤起） -->
  <TripEditDialog v-if="showLogin" class="auth-dialog" :label="loginMode === 'login' ? '登录' : '注册'" :busy="busy" @close="closeLogin">
    <div class="modal">
      <div class="tabs">
        <button type="button" :disabled="busy" :class="{ on: loginMode === 'login' }" @click="changeLoginMode('login')">登录</button>
        <button type="button" :disabled="busy" :class="{ on: loginMode === 'register' }" @click="changeLoginMode('register')">注册</button>
      </div>
      <input v-model="form.username" :disabled="busy" aria-label="用户名" autocomplete="username" placeholder="用户名（3 位以上，字母/数字/中文）" />
      <input v-if="loginMode === 'register'" v-model="form.nickname" :disabled="busy" aria-label="昵称" autocomplete="nickname" placeholder="昵称（可留空）" />
      <input v-model="form.password" :disabled="busy" aria-label="密码" :autocomplete="loginMode === 'login' ? 'current-password' : 'new-password'" type="password" placeholder="密码（6 位以上）" @keyup.enter="submit" />
      <p v-if="err" class="err">{{ err }}</p>
      <button class="btn-main" :disabled="busy" @click="submit">
        {{ busy ? '请稍候…' : (loginMode === 'login' ? '登录' : '注册并登录') }}
      </button>
      <button type="button" class="close-login" :disabled="busy" @click="closeLogin">取消</button>
    </div>
  </TripEditDialog>
</template>

<style scoped>
.nav-toggle{min-width:44px;min-height:44px}.nav a:focus-visible,.nav button:focus-visible{outline:3px solid #679b7f;outline-offset:2px}@media(max-width:380px){.nav{padding:0 3%;gap:4px}.nav .logo{font-size:13px}.nav .nav-cta{padding:6px 8px;font-size:12px}.nav .user{font-size:12px;white-space:nowrap}}
.auth-warning { display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 5%;background:#fff6e5;color:#765d2f;font-size:13px; }
.auth-warning button { border:0;background:none;color:#1a6e50;text-decoration:underline;cursor:pointer; }
.auth-warning button:disabled { opacity:.5; }
.auth-dialog { width: min(364px, calc(100% - 32px)); }
.modal {
  width: 100%; background: #fff; border-radius: 14px; padding: 22px;
  display: flex; flex-direction: column; gap: 12px;
}
.tabs { display: flex; gap: 6px; margin-bottom: 4px; }
.tabs button {
  flex: 1; text-align: center; padding: 8px 0; border-radius: 8px;
  border: 0; background: #f2f1ed; color: #666; cursor: pointer; font-size: 13.5px;
}
.tabs button.on { background: var(--green); color: #fff; }
.close-login { border:0; background:none; color:#557568; padding:5px; }
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
.mobile-nav { display: none; }
@media (max-width: 640px) {
  :global(body) { padding-bottom: 72px; }
  .mobile-nav { position: fixed; z-index: 20; bottom: 0; left: 0; right: 0; display: flex; justify-content: space-around; align-items: center; background: #fff; border-top: 1px solid #e2eae4; padding: 7px 0 max(7px, env(safe-area-inset-bottom)); }
  .mobile-nav a { min-width: 52px; text-align: center; color: #87988d; font-size: 10px; }
  .mobile-nav a span { display: block; font-size: 21px; line-height: 24px; }
  .mobile-nav a.router-link-exact-active { color: #1a6e50; font-weight: 700; }
  .mobile-nav .mobile-new span { margin: auto; width: 30px; height: 27px; border-radius: 8px; color: white; background: #1a6e50; }
}
</style>
