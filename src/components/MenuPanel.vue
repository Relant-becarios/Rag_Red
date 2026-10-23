<template>
  <div class="menu-panel-overlay" :class="{ open: uiStore.isMenuOpen }" @click="uiStore.closeAll">
    <aside class="menu-panel" :class="{ open: uiStore.isMenuOpen }" @click.stop>
      <div class="menu-header">
        <h3 class="menu-title">Menú</h3>
        <button class="btn-close" @click="uiStore.closeAll" title="Cerrar menú">✕</button>
      </div>

      <div class="menu-body">
        <div class="menu-items-list">
          <!-- BONTÓN ASISTENTE IA (RAG) -->
          <button class="menu-item ai-item" @click="abrirAsistenteIA">
            <img src="/Estrella icono (1).png" alt="Asistente IA" class="icon-img" />
            <span class="label-text">Asistente IA</span>
          </button>

          <button class="menu-item" @click="navegarA('/calculadora')">
            <span class="icon">🧮</span>
            <span class="label-text">Calculadora de Dosificación</span>
          </button>

          <button class="menu-item" @click="navegarA('/visor-3d')">
            <span class="icon">🧊</span>
            <span class="label-text">Visor STL 3D</span>
          </button>

          <!-- Solo visible para usuarios con rol admin -->
          <button v-if="esAdmin" class="menu-item" @click="navegarA('/admin/pedidos')">
            <span class="icon">📦</span>
            <span class="label-text">Gestión de Pedidos</span>
          </button>
        </div>
      </div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const uiStore = useUiStore()
const authStore = useAuthStore()

const esAdmin = computed(() => authStore.esAdmin)

const navegarA = (ruta: string) => {
  uiStore.closeAll()
  router.push(ruta)
}

// Redirige a la vista completa del Asistente RAG
const abrirAsistenteIA = () => {
  uiStore.closeAll()
  router.push('/asistente')
}
</script>

<style scoped>
.menu-panel-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.7);
  opacity: 0;
  visibility: hidden;
  transition: all 0.3s ease;
  z-index: 30000;
}

.menu-panel-overlay.open {
  opacity: 1;
  visibility: visible;
}

.menu-panel {
  position: fixed;
  top: 0;
  right: -320px;
  width: 100%;
  max-width: 300px;
  height: 100vh;
  background: var(--bg-panel, #ffffff);
  border-left: 1px solid var(--border, #d1d5da);
  color: var(--text-main, #1c1e21);
  display: flex;
  flex-direction: column;
  transition: right 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: -10px 0 30px rgba(0, 0, 0, 0.3);
}

.menu-panel.open {
  right: 0;
}

.menu-header {
  position: relative;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border, #d1d5da);
  display: flex;
  align-items: center;
  justify-content: center;
}

.menu-title {
  margin: 0;
  font-size: 1.7rem;
  font-weight: 800;
  text-align: center;
  color: var(--text-main, #1c1e21);
}

.btn-close {
  position: absolute;
  right: 18px;
  top: 10px;
  background: transparent;
  border: none;
  color: #ff0000;
  font-size: 1.6rem;
  font-weight: 900;
  cursor: pointer;
  padding: 0;
  line-height: 1;
  transition:
    transform 0.2s ease,
    color 0.2s ease;
}

.btn-close:hover {
  color: #cc0000;
  transform: scale(1.15);
}

.menu-body {
  flex: 1;
  padding: 20px 16px;
  overflow-y: auto;
}

.menu-items-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 12px 14px;
  background: var(--bg-input, #f8f9fa);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 10px;
  color: var(--text-main, #1c1e21);
  font-size: 0.95rem;
  font-weight: 700;
  cursor: pointer;
  text-align: left;
  transition: all 0.2s ease;
  box-sizing: border-box;
}

.menu-item:hover {
  background: rgba(229, 46, 46, 0.12);
  border-color: rgba(229, 46, 46, 0.4);
  color: #ff0000;
  transform: translateY(-1px);
}

.menu-item.ai-item {
  background: rgba(229, 46, 46, 0.08);
  border-color: rgba(229, 46, 46, 0.3);
  color: #ff0000;
}

.menu-item.ai-item:hover {
  background: rgba(229, 46, 46, 0.18);
}

.menu-item img.icon-img,
.icon-img {
  width: 24px !important;
  height: 24px !important;
  max-width: 24px !important;
  max-height: 24px !important;
  object-fit: contain;
  flex-shrink: 0;
  display: block;
}

.icon {
  font-size: 1.2rem;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
}

.label-text {
  line-height: 1.2;
}
</style>
