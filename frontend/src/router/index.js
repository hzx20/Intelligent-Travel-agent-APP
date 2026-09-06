import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', component: () => import('../views/HomeView.vue') },
  { path: '/spots', component: () => import('../views/SpotsView.vue') },
  { path: '/spots/:id', component: () => import('../views/SpotDetailView.vue') },
  { path: '/me', component: () => import('../views/MeView.vue') },
  { path: '/admin', component: () => import('../views/AdminView.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})
