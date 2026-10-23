<template>
  <div class="page-container">
    <h1>Vista Explosiva</h1>
    <p>Visualizador interactivo de componentes.</p>
    <StlView class="vue" />
  </div>
</template>

<script setup lang="ts">
import StlView from '@/views/StlView.vue'
</script>

<style scoped>
.page-container {
  padding: 20px;
  color: white;
}
</style>
