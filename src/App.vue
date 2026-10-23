<template>
  <div id="app-layout">
    <!-- PANTALLA DE CARGA DUAL (BLANCA EN MODO CLARO / NEGRA EN MODO OSCURO) -->
    <Transition name="fade">
      <div v-if="cargandoPagina" class="reload-overlay">
        <div class="brand-loader-container">
          <!-- Anillos de pulso de fondo -->
          <div class="pulse-ring"></div>
          <div class="pulse-ring ring-delay"></div>

          <!-- Logo de la marca -->
          <img src="@/assets/logo-relant.png" alt="Relant" class="reload-logo" />

          <!-- Barra de carga fluida -->
          <div class="loading-bar-track">
            <div class="loading-bar-fill"></div>
          </div>
        </div>
      </div>
    </Transition>

    <div
      v-if="
        uiStore.isCartOpen || uiStore.isMenuOpen || uiStore.isAuthModalOpen || uiStore.isChatOpen
      "
      class="overlay"
      @click="uiStore.closeAll"
    ></div>

    <MenuPanel />
    <CartPanel />
    <AuthModal />
    <ChatPanel />

    <!-- Botón Flotante para Alternar Tema -->
    <ThemeToggleFloating />
    <!-- Botón Flotante del Asistente IA (RAG + catálogo) -->
    <AssistantFab />

    <RouterView />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { RouterView } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import { useMarketStore } from '@/stores/market'
import CartPanel from '@/components/CartPanel.vue'
import MenuPanel from '@/components/MenuPanel.vue'
import AuthModal from '@/components/AuthModal.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import ThemeToggleFloating from '@/components/ThemeToggleFloating.vue'
import AssistantFab from '@/components/AssistantFab.vue'

const uiStore = useUiStore()
const marketStore = useMarketStore()

const cargandoPagina = ref(true)

const activarLoaderRecarga = () => {
  cargandoPagina.value = true
}

const prevenirContextMenu = (e: MouseEvent) => e.preventDefault()

const prevenirDevToolsKey = (e: KeyboardEvent) => {
  if (
    e.key === 'F12' ||
    (e.ctrlKey && e.shiftKey && ['I', 'J', 'C'].includes(e.key.toUpperCase())) ||
    (e.ctrlKey && e.key.toUpperCase() === 'U')
  ) {
    e.preventDefault()
  }
}

// Sincronización inmediata al importar el componente
const temaInmediato = localStorage.getItem('theme') || localStorage.getItem('data-theme') || 'light'
document.documentElement.setAttribute('data-theme', temaInmediato)
document.body.setAttribute('data-theme', temaInmediato)

onMounted(() => {
  const tema = localStorage.getItem('theme') || localStorage.getItem('data-theme') || 'light'
  document.documentElement.setAttribute('data-theme', tema)
  document.body.setAttribute('data-theme', tema)

  marketStore.iniciarSincronizacionAuto(60)

  document.addEventListener('contextmenu', prevenirContextMenu)
  document.addEventListener('keydown', prevenirDevToolsKey)
  window.addEventListener('beforeunload', activarLoaderRecarga)

  setTimeout(() => {
    cargandoPagina.value = false
  }, 1200)
})

onUnmounted(() => {
  marketStore.detenerSincronizacionAuto()

  document.removeEventListener('contextmenu', prevenirContextMenu)
  document.removeEventListener('keydown', prevenirDevToolsKey)
  window.removeEventListener('beforeunload', activarLoaderRecarga)
})
</script>

<style>
/* --- VARIABLES GLOBALES DE CSS --- */
:root,
html[data-theme='light'],
body[data-theme='light'] {
  --bg-main: #f4f6f8;
  --bg-panel: #ffffff;
  --bg-input: #e9ecef;
  --text-main: #1c1e21;
  --text-muted: #6a737d;
  --border: #d1d5da;
  --accent: #d32f2f;
  --accent-hover: #b71c1c;

  --fondo-principal: #ffffff;
  --fondo-tarjeta: #ffffff;
  --color-texto: #1c1e21;
  --borde: #d1d5da;
}

[data-theme='dark'],
:root[data-theme='dark'],
html[data-theme='dark'],
body[data-theme='dark'] {
  --bg-main: #0f1215;
  --bg-panel: #161b22;
  --bg-input: #0d1117;
  --text-main: #ffffff;
  --text-muted: #8b949e;
  --border: #30363d;
  --accent: #ff0000;
  --accent-hover: #cc0000;

  --fondo-principal: #0f1215;
  --fondo-tarjeta: #161b22;
  --color-texto: #ffffff;
  --borde: #30363d;
}

body {
  margin: 0;
  font-family: 'Segoe UI', Arial, sans-serif;
  background: var(--bg-main);
  color: var(--text-main);
  overflow-x: hidden;
  transition:
    background 0.3s ease,
    color 0.3s ease,
    border-color 0.3s ease;
}

/* ===================================================
   1. MODO CLARO: PANTALLA DE CARGA BLANCA CON LOGO OSCURO
   =================================================== */
.reload-overlay {
  position: fixed;
  inset: 0;
  z-index: 99999;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background-color: #ffffff !important; /* Fondo Blanco en Modo Claro */
  transition: background-color 0.2s ease;
}

.reload-overlay .reload-logo {
  width: 320px;
  max-width: 80vw;
  height: auto;
  z-index: 2;
  animation: logoBreath 2s ease-in-out infinite;
  filter: invert(1) hue-rotate(180deg) drop-shadow(0 0 12px rgba(229, 46, 46, 0.25));
  transition: filter 0.2s ease;
}

.brand-loader-container {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.pulse-ring {
  position: absolute;
  top: 35%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 180px;
  height: 180px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(229, 46, 46, 0.35) 0%, rgba(229, 46, 46, 0) 70%);
  animation: ringPulse 2s cubic-bezier(0.215, 0.61, 0.355, 1) infinite;
  z-index: 1;
}

.ring-delay {
  animation-delay: 0.6s;
}

.loading-bar-track {
  width: 180px;
  height: 3px;
  background: rgba(0, 0, 0, 0.15);
  border-radius: 4px;
  overflow: hidden;
  margin-top: 25px;
  position: relative;
  z-index: 2;
}

.loading-bar-fill {
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, transparent, #e52e2e, #1c1e21, #e52e2e, transparent);
  animation: loadingShift 1.4s ease-in-out infinite;
}

/* ===================================================
   2. MODO OSCURO: PANTALLA DE CARGA NEGRA (#0F1215) CON LOGO BLANCO
   =================================================== */
html[data-theme='dark'] .reload-overlay,
body[data-theme='dark'] .reload-overlay,
:root[data-theme='dark'] .reload-overlay,
[data-theme='dark'] .reload-overlay {
  background-color: #0f1215 !important; /* Fondo Negro en Modo Oscuro */
}

html[data-theme='dark'] .reload-overlay .reload-logo,
body[data-theme='dark'] .reload-overlay .reload-logo,
:root[data-theme='dark'] .reload-overlay .reload-logo,
[data-theme='dark'] .reload-overlay .reload-logo {
  filter: invert(0) drop-shadow(0 0 18px rgba(229, 46, 46, 0.6)) !important; /* Logo Blanco */
}

html[data-theme='dark'] .loading-bar-track,
body[data-theme='dark'] .loading-bar-track,
[data-theme='dark'] .loading-bar-track {
  background: rgba(255, 255, 255, 0.15) !important;
}

html[data-theme='dark'] .loading-bar-fill,
body[data-theme='dark'] .loading-bar-fill,
[data-theme='dark'] .loading-bar-fill {
  background: linear-gradient(
    90deg,
    transparent,
    #e52e2e,
    #ffffff,
    #e52e2e,
    transparent
  ) !important;
}

@keyframes logoBreath {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.03);
  }
}

@keyframes ringPulse {
  0% {
    width: 100px;
    height: 100px;
    opacity: 0.8;
  }
  100% {
    width: 320px;
    height: 320px;
    opacity: 0;
  }
}

@keyframes loadingShift {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(100%);
  }
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.4s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.8);
  z-index: 20000;
}
</style>
