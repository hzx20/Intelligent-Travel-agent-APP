import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', component: () => import('../views/TripsView.vue') },
  { path: '/inspiration', component: () => import('../views/HomeView.vue') },
  { path: '/travel-data', component: () => import('../views/TravelDataView.vue') },
  { path: '/trips/new', component: () => import('../views/TripCreateView.vue') },
  { path: '/trips/:id', component: () => import('../views/TripDetailView.vue') },
  { path: '/share', component: () => import('../views/TripSharedView.vue'), props: { mode: 'view' } },
  { path: '/invite', component: () => import('../views/TripSharedView.vue'), props: { mode: 'edit' } },
  { path: '/spots', component: () => import('../views/SpotsView.vue') },
  { path: '/spots/:id', component: () => import('../views/SpotDetailView.vue') },
  { path: '/plan', component: () => import('../views/PlanView.vue') },
  { path: '/guides', component: () => import('../views/GuidesView.vue') },
  { path: '/guides/new', component: () => import('../views/GuideEditView.vue') },
  { path: '/guides/:id', component: () => import('../views/GuideDetailView.vue') },
  { path: '/guides/:id/edit', component: () => import('../views/GuideEditView.vue') },
  { path: '/me', component: () => import('../views/MeView.vue') },
  { path: '/footprints', component: () => import('../views/FootprintsView.vue') },
  { path: '/admin', component: () => import('../views/AdminView.vue') },
]

export default createRouter({
  // 生产环境挂在 /travel/ 子路径下，这里跟随 Vite 的 BASE_URL，
  // 否则刷新 /travel/plan 会被当成站内根路由而 404
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})
