<template>
  <nav class="navbar">
    <router-link to="/" @click="limpiarInicio" style="text-decoration: none">
      <!-- Muestra el logo blanco si está en modo oscuro -->
      <img
        v-if="uiStore.temaActual === 'dark'"
        src="/MARCA CON SLOGAN CON FONDO BLANCO .png"
        alt="RELANT Logo"
        class="logo-nav-img"
      />

      <!-- Muestra el logo normal si está en modo claro -->
      <img
        v-else
        src="/LOGOTIPO RELANT CON LOGO 242 (1).png"
        alt="RELANT Logo"
        class="logo-nav-img"
      />
    </router-link>
    <!-- BARRA DE BÚSQUEDA Y DROPDOWN -->
    <div class="search-container" v-if="mostrarFunciones">
      <div class="dropdown">
        <button type="button" class="btn-cat" @click.stop="showCats = !showCats">
          <span class="cat-text">{{ marketStore.selectedCategory || 'Todas' }}</span>
          <span class="arrow">▼</span>
        </button>

        <div class="dropdown-content" :class="{ show: showCats }" @click.stop>
          <a @click="seleccionarCat('Todas')">Todas</a>
          <a v-for="cat in categoriasDisponibles" :key="cat" @click="seleccionarCat(cat)">
            {{ cat }}
          </a>
        </div>
      </div>

      <input
        type="text"
        class="search-input"
        placeholder="Escribe y presiona Enter para buscar..."
        v-model="marketStore.searchQuery"
        @keyup.enter="ejecutarBusqueda"
      />
    </div>

    <div v-else style="flex-grow: 1"></div>

    <div class="nav-actions">
      <!-- BOTÓN MI PERFIL -->
      <router-link
        v-if="authStore.usuarioActual"
        to="/perfil"
        class="btn-profile"
        title="Ir a Mi Perfil"
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
          <circle cx="12" cy="7" r="4"></circle>
        </svg>
        <span>Mi Perfil</span>
      </router-link>

      <div v-else class="login-trigger" @click.stop="uiStore.toggleAuthModal">Ingresar</div>

      <!-- ACCIONES DEL CATÁLOGO -->
      <div class="catalog-only-actions" v-if="mostrarFunciones">
        <!-- Carrito -->
        <div class="cart-icon" @click.stop="uiStore.toggleCart" title="Carrito de Compras">
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <circle cx="9" cy="21" r="1"></circle>
            <circle cx="20" cy="21" r="1"></circle>
            <path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"></path>
          </svg>
          <span class="cart-badge">{{ cartStore.totalItems }}</span>
        </div>

        <!-- Menú Hamburguesa -->
        <div class="icon-hamburguesa" @click.stop="uiStore.toggleMenu" title="Menú">
          <span></span><span></span><span></span>
        </div>
      </div>

      <!-- BOTÓN SALIR -->
      <button
        v-if="authStore.usuarioActual"
        class="btn-logout"
        @click.stop="authStore.cerrarSesion"
        title="Cerrar sesión"
      >
        <svg
          class="power-icon"
          viewBox="0 0 24 24"
          width="20"
          height="20"
          stroke="currentColor"
          stroke-width="2.5"
          fill="none"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M18.36 6.64a9 9 0 1 1-12.73 0"></path>
          <line x1="12" y1="2" x2="12" y2="12"></line>
        </svg>
        <span class="logout-text">Salir</span>
      </button>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useCartStore } from '@/stores/cart'
import { useMarketStore } from '@/stores/market'
import { useUiStore } from '@/stores/ui'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const cartStore = useCartStore()
const marketStore = useMarketStore()
const uiStore = useUiStore()
const authStore = useAuthStore()

const showCats = ref(false)
const emit = defineEmits(['buscar'])

const mostrarFunciones = computed(() => route.path === '/catalogo')

// Obtiene la lista de categorías sin importar el nombre en el store
const categoriasDisponibles = computed<string[]>(() => {
  const storeExt = marketStore as unknown as {
    categorias?: string[]
    availableCategories?: string[]
  }
  return storeExt.categorias || storeExt.availableCategories || []
})

const seleccionarCat = (cat: string) => {
  marketStore.selectedCategory = cat
  showCats.value = false
  emit('buscar')
}

const ejecutarBusqueda = () => {
  showCats.value = false
  emit('buscar')
}

const limpiarInicio = () => {
  marketStore.searchQuery = ''
  marketStore.selectedCategory = 'Todas'
  emit('buscar')
}

const cerrarMenuFuera = () => {
  showCats.value = false
}

onMounted(() => {
  window.addEventListener('click', cerrarMenuFuera)
})

onUnmounted(() => {
  window.removeEventListener('click', cerrarMenuFuera)
})
</script>

<style scoped>
.navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 5%;
  background: var(--bg-panel, #ffffff);
  border-bottom: 1px solid var(--border, #d1d5da);
  position: sticky;
  top: 0;
  z-index: 100;
  color: var(--text-main, #1c1e21);
  transition:
    background 0.3s ease,
    border-color 0.3s ease,
    color 0.3s ease;
}

.logo-nav-img {
  height: 35px;
  width: auto;
  object-fit: contain;
  cursor: pointer;
  display: block;
}

.search-container {
  display: flex;
  align-items: center;
  flex-grow: 1;
  max-width: 580px;
  height: 42px;
  margin: 0 20px;
  background: var(--bg-input, #eef2f5);
  border: 1px solid var(--border, #d1d5da);
  border-radius: 22px;
  position: relative;
}

.dropdown {
  position: relative;
  display: flex;
  align-items: center;
  height: 100%;
}

.btn-cat {
  background: #ff0000;
  color: #ffffff;
  border: none;
  padding: 0 16px;
  font-weight: 800;
  cursor: pointer;
  font-size: 13px;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 110px;
  outline: none;
  border-radius: 21px 0 0 21px;
  transition: background 0.2s ease;
}

.btn-cat:hover {
  background: #d32f2f;
}

.cat-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 110px;
}

.arrow {
  font-size: 10px;
}

.search-input {
  flex-grow: 1;
  border: none;
  background: transparent;
  color: inherit;
  padding: 0 18px;
  outline: none;
  font-size: 14px;
  height: 100%;
  width: 100%;
  border-radius: 0 21px 21px 0;
}

.dropdown-content {
  display: none;
  position: absolute;
  background: var(--bg-panel, #ffffff);
  min-width: 220px;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.25);
  z-index: 99999;
  border: 1px solid var(--border, #d1d5da);
  border-radius: 8px;
  top: calc(100% + 6px);
  left: 0;
  max-height: 300px;
  overflow-y: auto;
}

.dropdown-content.show {
  display: block;
}

.dropdown-content a {
  color: inherit;
  padding: 12px 16px;
  text-decoration: none;
  display: block;
  cursor: pointer;
  font-size: 14px;
  border-bottom: 1px solid var(--border, #d1d5da);
}

.dropdown-content a:hover {
  background: #ff0000;
  color: white;
}

.nav-actions {
  display: flex;
  align-items: center;
  gap: 20px;
}

.catalog-only-actions {
  display: flex;
  align-items: center;
  gap: 20px;
  color: inherit;
}

.cart-icon {
  position: relative;
  cursor: pointer;
  display: flex;
  align-items: center;
  color: inherit;
}

.cart-badge {
  position: absolute;
  top: -8px;
  right: -8px;
  background: #ff0000;
  color: white;
  border-radius: 50%;
  width: 18px;
  height: 18px;
  font-size: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
}

.icon-hamburguesa {
  width: 26px;
  height: 18px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  cursor: pointer;
  color: inherit;
}

.icon-hamburguesa span {
  display: block;
  width: 100%;
  height: 3px;
  background-color: currentColor;
  border-radius: 2px;
  transition: 0.3s;
}

.login-trigger {
  cursor: pointer;
  font-size: 14px;
  font-weight: bold;
  color: inherit;
  background: var(--bg-input, #eef2f5);
  border: 1px solid var(--border, #d1d5da);
  padding: 6px 14px;
  border-radius: 20px;
  transition: all 0.2s ease;
}

.login-trigger:hover {
  background: #ff0000;
  color: white;
  border-color: #ff0000;
}

.btn-profile {
  display: flex;
  align-items: center;
  gap: 6px;
  text-decoration: none;
  color: inherit;
  font-size: 13px;
  font-weight: 800;
  padding: 6px 14px;
  border-radius: 20px;
  background: var(--bg-input, #eef2f5);
  border: 1px solid var(--border, #d1d5da);
  transition: all 0.2s ease;
}

.btn-profile:hover {
  background: #ff0000;
  color: #ffffff;
  border-color: #ff0000;
}

.btn-logout {
  background: transparent;
  border: none;
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  padding: 2px;
  gap: 2px;
  transition:
    transform 0.2s ease,
    opacity 0.2s ease;
}

.btn-logout:hover {
  transform: scale(1.1);
  opacity: 0.85;
}

.power-icon {
  stroke: #ff0000;
}

.logout-text {
  color: #ff0000;
  font-size: 11px;
  font-weight: 800;
  line-height: 1;
}

:global([data-theme='dark']) .navbar {
  background: #181b1f;
  border-bottom-color: #30363d;
  color: #ffffff;
}

:global([data-theme='dark']) .search-container {
  background: #22262b;
  border-color: #30363d;
}

:global([data-theme='dark']) .search-input {
  color: #ffffff;
}

:global([data-theme='dark']) .search-input::placeholder {
  color: #a0a6ac;
}

:global([data-theme='dark']) .btn-profile,
:global([data-theme='dark']) .login-trigger {
  background: #22262b;
  color: #ffffff;
  border-color: #30363d;
}

:global([data-theme='dark']) .dropdown-content {
  background: #181b1f;
  border-color: #30363d;
}

:global([data-theme='dark']) .dropdown-content a {
  color: #ffffff;
  border-bottom-color: #30363d;
}
</style>
