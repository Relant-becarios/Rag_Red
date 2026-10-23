import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue'),
    },
    {
      path: '/catalogo',
      name: 'catalogo',
      component: () => import('@/views/CatalogoView.vue'),
    },
    {
      path: '/perfil',
      name: 'perfil',
      component: () => import('@/views/ProfileView.vue'),
    },
    {
      path: '/asistente',
      name: 'asistente',
      component: () => import('@/views/AsistenteView.vue'),
    },
    {
      path: '/checkout',
      name: 'checkout',
      component: () => import('@/views/CheckoutView.vue'),
    },
    {
      path: '/calculadora',
      name: 'calculadora',
      component: () => import('@/views/CalculadoraView.vue'),
    },
    {
      path: '/visor-3d',
      name: 'visor3d',
      component: () => import('@/views/StlView.vue'),
    },
    {
      path: '/admin/pedidos',
      name: 'admin-pedidos',
      component: () => import('@/views/AdminOrdenes.vue'),
      meta: { requiereAdmin: true },
    },
  ],
})

// Guard de navegación asíncrono
router.beforeEach(async (to, from, next) => {
  const authStore = useAuthStore()

  // Espera a que Firebase responda y cargue tu rol en memoria
  await authStore.authReady

  if (to.meta.requiereAdmin && !authStore.esAdmin) {
    next('/catalogo')
  } else {
    next()
  }
})

export default router
