<template>
  <div
    class="theme-floating-btn"
    @click="uiStore.toggleTema()"
    :title="uiStore.isDarkTheme ? 'Cambiar a modo claro' : 'Cambiar a modo oscuro'"
  >
    <span class="icon">{{ uiStore.isDarkTheme ? '☀️' : '🌙' }}</span>
  </div>
</template>

<script setup lang="ts">
import { useUiStore } from '@/stores/ui'

const uiStore = useUiStore()
</script>

<style scoped>
.theme-floating-btn {
  position: fixed;
  bottom: 25px;
  right: 25px;
  width: 55px;
  height: 55px;
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #d1d5da);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 15000;
  box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
  transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
  user-select: none;
}

.theme-floating-btn:hover {
  transform: scale(1.1) translateY(-5px);
  background: var(--bg-input, #e9ecef);
  box-shadow: 0 15px 35px rgba(229, 46, 46, 0.35);
  border-color: var(--accent, #d32f2f);
}

.icon {
  font-size: 24px;
}
</style>
