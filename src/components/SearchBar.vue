<template>
  <div class="search-container">
    <input type="text" placeholder="Buscar por Nombre..." v-model="busqueda" />
    <select v-model="categoriaSeleccionada">
      <option value="Todos">Todos</option>
      <option value="Bombas">Bombas</option>
      <option value="Accesorios">Accesorios</option>
    </select>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const busqueda = ref('')
const categoriaSeleccionada = ref('Todos')
</script>

<style scoped>
.search-container {
  display: flex;
  gap: 10px;
}
input,
select {
  padding: 8px;
  border-radius: 5px;
  border: 1px solid #30363d;
  background: #0d1117;
  color: white;
}
</style>
