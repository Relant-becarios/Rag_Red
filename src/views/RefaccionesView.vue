<template>
  <div class="refacciones-page">
    <NavBar />

    <main class="content-container">
      <!-- BOTÓN VOLVER -->
      <button class="btn-back" @click="$router.push('/catalogo')">← Volver al Catálogo</button>

      <!-- ESTADO 1: CARGANDO -->
      <div v-if="cargando" class="state-card">
        <p>Cargando información del equipo...</p>
      </div>

      <!-- ESTADO 2: PRODUCTO NO ENCONTRADO -->
      <div v-else-if="!producto" class="state-card error">
        <h2>⚠️ Equipo no encontrado</h2>
        <p>No se encontró información para el código o nombre especificado.</p>
      </div>

      <!-- ESTADO 3: DETALLE DEL PRODUCTO -->
      <div v-else class="refacciones-grid">
        <header class="page-header">
          <h2>Código de Equipo: {{ producto.Producto || producto.id }}</h2>
        </header>

        <div class="panels-layout">
          <!-- Panel Izquierdo: Diagrama -->
          <section class="panel-box">
            <h3>Diagrama Explosionado</h3>
            <div class="diagram-container">
              <img
                v-if="imagenDiagrama"
                :src="imagenDiagrama"
                alt="Diagrama explosionado"
                class="diagram-img"
              />
              <div v-else class="no-data">
                ⚠️ Este equipo no tiene un diagrama asignado en la Base de Datos.
              </div>
            </div>
          </section>

          <!-- Panel Derecho: Kits -->
          <section class="panel-box">
            <h3>Kits de Repuestos</h3>
            <div class="kits-container">
              <div v-if="!tieneKits" class="no-data">
                No hay kits de refacciones vinculados a este equipo en la base de datos.
              </div>
              <div v-else class="kits-list">
                <!-- Aquí se iteran los kits si existen -->
              </div>
            </div>
          </section>
        </div>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { fetchProductos, type Producto } from '@/api/inventory'
import NavBar from '@/components/NavBar.vue'

const route = useRoute()
const cargando = ref(true)
const producto = ref<Producto | null>(null)

const cargarEquipo = async () => {
  cargando.value = true
  try {
    const catalogo = await fetchProductos()

    // Decodifica %20, acentos y limpia espacios de la URL
    const idBusqueda = decodeURIComponent(String(route.params.id || ''))
      .trim()
      .toLowerCase()

    // Búsqueda tolerante por ID, SKU o Nombre de producto
    const encontrado = catalogo.find((p) => {
      const pId = String(p.id || p.ID || '')
        .trim()
        .toLowerCase()
      const pSku = String(p.sku || '')
        .trim()
        .toLowerCase()
      const pNombre = String(p.Producto || p.nombre || '')
        .trim()
        .toLowerCase()

      return pId === idBusqueda || pSku === idBusqueda || pNombre === idBusqueda
    })

    producto.value = encontrado || null
  } catch (error) {
    console.error('Error cargando refacciones:', error)
    producto.value = null
  } finally {
    cargando.value = false
  }
}

onMounted(() => {
  cargarEquipo()
})

const imagenDiagrama = computed<string | null>(() => {
  const raw = producto.value?.Imagen_Explosionada_URL ?? producto.value?.imagen_explosionada ?? null

  if (typeof raw !== 'string') {
    return null
  }

  return raw.trim() || null
})

const tieneKits = computed(() => {
  const kits = producto.value?.Kits || producto.value?.kits
  return Array.isArray(kits) && kits.length > 0
})
</script>

<style scoped>
.refacciones-page {
  min-height: 100vh;
  background: var(--bg-main, #09090b);
  color: var(--text-main, #ffffff);
}
.content-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 30px 20px;
}
.btn-back {
  background: var(--bg-panel, #18181b);
  border: 1px solid var(--border, #27272a);
  color: var(--text-main, #ffffff);
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 20px;
  font-weight: bold;
}
.btn-back:hover {
  border-color: #ff0000;
}
.state-card {
  text-align: center;
  padding: 60px;
  background: var(--bg-panel, #18181b);
  border: 1px solid var(--border, #27272a);
  border-radius: 12px;
  margin-top: 20px;
}
.state-card.error h2 {
  color: #ff4444;
}
.page-header h2 {
  font-size: 1.2rem;
  margin-bottom: 25px;
  color: var(--text-muted, #a1a1aa);
}
.panels-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 25px;
}
.panel-box {
  background: var(--bg-panel, #18181b);
  border: 1px solid var(--border, #27272a);
  border-radius: 10px;
  padding: 20px;
}
.panel-box h3 {
  margin-top: 0;
  border-bottom: 2px solid #ff0000;
  padding-bottom: 8px;
  font-size: 1.1rem;
}
.diagram-container,
.kits-container {
  min-height: 250px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.diagram-img {
  max-width: 100%;
  max-height: 350px;
  object-fit: contain;
}
.no-data {
  color: var(--text-muted, #a1a1aa);
  text-align: center;
  font-size: 0.9rem;
}
@media (max-width: 768px) {
  .panels-layout {
    grid-template-columns: 1fr;
  }
}
</style>
