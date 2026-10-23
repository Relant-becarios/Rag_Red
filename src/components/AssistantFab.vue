<template>
  <button
    v-if="visible"
    class="assistant-fab"
    title="Asistente IA: te ayuda a encontrar el producto"
    aria-label="Abrir asistente IA"
    @click="uiStore.toggleChat()"
  >
    <!-- Se actualizó el nombre de la imagen a Estrella icono.png -->
    <img src="/Estrella icono.png" alt="Estrella IA" />
    <span class="label">Asistente IA</span>
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useUiStore } from '@/stores/ui'

const uiStore = useUiStore()
const route = useRoute()

// El botón SOLO será visible en la ruta 'catalogo' y si el panel lateral no está abierto
const visible = computed(() => route.name === 'catalogo' && !uiStore.isChatOpen)
</script>

<style scoped>
.assistant-fab {
  position: fixed;
  right: 25px;
  bottom: 95px;
  z-index: 15000;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 52px;
  padding: 0 18px 0 12px;
  border: none;
  border-radius: 999px;
  background: var(--accent, #d32f2f);
  color: #fff;
  font-weight: 800;
  font-size: 0.85rem;
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.22);
  transition: transform 0.2s ease;
}
.assistant-fab:hover {
  transform: translateY(-2px);
}
.assistant-fab img {
  width: 28px;
  height: 28px;
  object-fit: contain;
}
@media (max-width: 600px) {
  .assistant-fab {
    padding: 0 12px;
  }
  .assistant-fab .label {
    display: none;
  }
}
</style>