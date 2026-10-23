<template>
  <div v-if="marketStore.isModalOpen" class="modal-overlay" @click.self="marketStore.closeModal">
    <div class="modal-content">
      <div class="modal-header">
        <h2 style="color: var(--accent); margin: 0; letter-spacing: 1px;">FICHA TÉCNICA</h2>
        <span @click="marketStore.closeModal" class="close-btn">✕</span>
      </div>

      <div class="modal-body" v-if="producto">
        <!-- SECCIÓN DE GALERÍA DE IMÁGENES INTERACTIVA CON ZOOM -->
        <div class="modal-gallery">
          <!-- VISTA PRINCIPAL GRANDE CON LUPA/ZOOM TIPO AMAZON -->
          <div
            class="main-img-box"
            @mousemove="handleMouseMove"
            @mouseleave="handleMouseLeave"
          >
            <img
              :src="imagenSeleccionada || 'https://via.placeholder.com/300'"
              class="modal-img-main"
              :style="zoomStyle"
            />
          </div>

          <!-- MINIATURAS INTERACTIVAS -->
          <div v-if="galeria.length > 1" class="thumbnails-row">
            <div
              v-for="(img, idx) in galeria"
              :key="idx"
              :class="['thumb-card', { active: imagenSeleccionada === img.url }]"
              @click="imagenSeleccionada = img.url"
            >
              <img :src="img.url" :alt="img.label" />
              <span class="thumb-label">{{ img.label }}</span>
            </div>
          </div>
        </div>

        <!-- INFORMACIÓN Y DETALLES DEL PRODUCTO -->
        <div class="modal-info">
          <h3 class="producto-titulo">{{ producto.Producto || (producto as Record<string, unknown>)['Producto '] }}</h3>

          <p class="desc-text">
            {{ (producto as Record<string, unknown>).Descripción || (producto as Record<string, unknown>).Descripcion || (producto as Record<string, unknown>).Detalle || 'Sin descripción técnica registrada en la base de datos.' }}
          </p>

          <div class="specs-grid">
            <div class="spec-item">
              <strong>CATEGORÍA:</strong> <span>{{ producto.Categoria || 'N/A' }}</span>
            </div>
            <div class="spec-item">
              <strong>SKU / ID:</strong> <span>{{ producto.id || producto.ID || 'N/A' }}</span>
            </div>
            <div class="spec-item">
              <strong>NO. DE PARTE:</strong> <span>{{ (producto as Record<string, unknown>)['no. De parte'] || producto.id || 'N/A' }}</span>
            </div>
            <div class="spec-item">
              <strong>STOCK:</strong> <span>{{ producto.Stock ?? 0 }}</span>
            </div>
            <div class="spec-item">
              <strong>PRECIO:</strong> <span class="price-text">${{ Number(producto.Precio || 0).toFixed(2) }} USD</span>
            </div>

            <div class="spec-item" v-for="spec in especificacionesTecnicas" :key="spec.etiqueta">
              <strong>{{ spec.etiqueta.toUpperCase() }}:</strong> <span>{{ spec.valor }}</span>
            </div>
          </div>

          <div class="modal-actions">
            <button
              class="btn-primary"
              :disabled="stockNumerico <= 0"
              @click="añadirAlCarrito"
            >
              {{ stockNumerico <= 0 ? 'PRODUCTO AGOTADO' : 'AÑADIR AL CARRITO DE COTIZACIÓN' }}
            </button>

            <button class="btn-refacciones" @click="irARefacciones">
              VER KITS Y REFACCIONES (VISTA EXPLOSIONADA)
            </button>

            <button class="btn-asistente" @click="preguntarAsistente">
              ✨ PREGUNTAR AL ASISTENTE SOBRE ESTE PRODUCTO
            </button>

            <button class="btn-secondary" @click="marketStore.closeModal">CERRAR FICHA TÉCNICA</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useMarketStore, type ProductoExtendido } from '@/stores/market'
import { useCartStore } from '@/stores/cart'
import { useUiStore } from '@/stores/ui'

const marketStore = useMarketStore()
const cartStore = useCartStore()
const uiStore = useUiStore()
const router = useRouter()

const producto = computed(() => marketStore.productoSeleccionado)

// Abre el asistente con este producto como contexto (registrarVisita ya lo dejó en el
// historial, así el asistente sabe qué está viendo el cliente)
const preguntarAsistente = () => {
  marketStore.closeModal()
  if (!uiStore.isChatOpen) uiStore.toggleChat()
}

// LÓGICA DE ZOOM INTERACTIVO TIPO AMAZON
const isZoomed = ref(false)
const transformOrigin = ref('center center')

const handleMouseMove = (e: MouseEvent) => {
  const target = e.currentTarget as HTMLElement
  if (!target) return
  const { left, top, width, height } = target.getBoundingClientRect()
  const x = ((e.clientX - left) / width) * 100
  const y = ((e.clientY - top) / height) * 100
  transformOrigin.value = `${x.toFixed(2)}% ${y.toFixed(2)}%`
  isZoomed.value = true
}

const handleMouseLeave = () => {
  isZoomed.value = false
}

const zoomStyle = computed(() => {
  if (!isZoomed.value) {
    return {
      transform: 'scale(1)',
      transformOrigin: 'center center',
      transition: 'transform 0.2s ease, transform-origin 0.1s ease'
    }
  }
  return {
    transform: 'scale(2.5)',
    transformOrigin: transformOrigin.value,
    transition: 'transform 0.05s ease-out, transform-origin 0.05s ease-out'
  }
})

// CONSTRUCCIÓN DINÁMICA DE LA GALERÍA DE IMÁGENES
const galeria = computed(() => {
  if (!producto.value) return []
  const prod = producto.value as Record<string, unknown>
  const items: { label: string; url: string }[] = []

  const front = prod.Imagen_Front_URL || prod.Photo_front_url || prod.Photo_front || prod.Imagen_Front
  const back = prod.Imagen_Back_URL || prod.Photo_back_url || prod.Photo_back || prod.Imagen_Back || prod.Imagen2 || prod.imagen2
  const explosion = prod.Imagen_Explosionada_URL || prod.imagen_explosionada || prod.Diagrama
  const general = prod.Imagen_URL || prod['Imagen URL'] || prod.Imagen || prod.imagen || prod.Foto

  if (front && typeof front === 'string' && front.startsWith('http')) {
    items.push({ label: 'Front', url: front })
  }
  if (back && typeof back === 'string' && back.startsWith('http')) {
    items.push({ label: 'Back', url: back })
  }
  if (explosion && typeof explosion === 'string' && explosion.startsWith('http')) {
    items.push({ label: 'Diagrama', url: explosion })
  }

  if (items.length === 0 && general && typeof general === 'string') {
    items.push({ label: 'Vista', url: general })
  }

  return items
})

const imagenSeleccionada = ref<string>('')

const stockNumerico = computed(() => {
  if (!producto.value) return 0
  const val = (producto.value as ProductoExtendido).Stock
  return typeof val === 'number' ? val : parseInt(String(val || 0), 10) || 0
})

watch(
  () => producto.value,
  () => {
    isZoomed.value = false
    if (galeria.value.length > 0 && galeria.value[0]) {
      imagenSeleccionada.value = galeria.value[0].url
    } else {
      const pExt = producto.value as ProductoExtendido | null
      const fallback = pExt?.Imagen_URL || pExt?.Imagen || pExt?.imagen || 'https://via.placeholder.com/300'
      imagenSeleccionada.value = typeof fallback === 'string' ? fallback : 'https://via.placeholder.com/300'
    }
  },
  { immediate: true }
)

const especificacionesTecnicas = computed(() => {
  if (!producto.value) return []

  const camposIgnorados = [
    'ID', 'id', 'Producto', 'Producto ', 'Descripción', 'Descripcion', 'Detalle',
    'Imagen_URL', 'Imagen URL', 'Imagen', 'imagen', 'IMAGEN', 'Foto', 'URL', 'url',
    'Precio', 'precio', 'Categoria', 'categoria', 'CATEGORIA',
    'Stock', 'stock', 'no. De parte', 'no. de parte', 'NO_DE_PARTE', 'no_de_parte',
    'Kits', 'kits', 'Imagen_Explosionada_URL', 'imagen_explosionada',
    'Manual_URL', 'Plano_URL', 'Imagen_Front_URL', 'Imagen_Back_URL',
    'Photo_front', 'Photo_back', 'Photo_front_url', 'Photo_back_url',
    'Imagen_Front', 'Imagen_Back', 'Imagen2', 'imagen2', 'vistoEn'
  ]

  const specs = []

  for (const [key, value] of Object.entries(producto.value)) {
    if (!camposIgnorados.includes(key) && value !== '' && value !== null && value !== undefined) {
      let etiquetaFormateada = key.replace(/_/g, ' ')
      etiquetaFormateada = etiquetaFormateada.charAt(0).toUpperCase() + etiquetaFormateada.slice(1)
      specs.push({ etiqueta: etiquetaFormateada, valor: String(value) })
    }
  }

  return specs
})

const añadirAlCarrito = () => {
  if (producto.value && stockNumerico.value > 0) {
    const prodStrict = producto.value as Record<string, unknown>
    const id = String(
      prodStrict.id ||
        prodStrict.ID ||
        prodStrict.SKU ||
        prodStrict['no. De parte'] ||
        prodStrict.NO_DE_PARTE ||
        prodStrict.Producto ||
        '',
    ).trim()

    if (id) {
      cartStore.agregarProducto(id, stockNumerico.value, 1)
      uiStore.toggleCart()
      marketStore.closeModal()
    }
  }
}

const irARefacciones = () => {
  if (producto.value) {
    const prodStrict = producto.value as Record<string, unknown>
    const id = prodStrict.id || prodStrict.ID || prodStrict.Producto || prodStrict['Producto ']
    marketStore.closeModal()
    router.push(`/refacciones/${id}`)
  }
}
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(4px);
  z-index: 10000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  box-sizing: border-box;
}

.modal-content {
  background: var(--bg-panel, #ffffff);
  width: 100%;
  max-width: 850px;
  border-radius: 12px;
  border: 1px solid var(--border, #e2e8f0);
  overflow: hidden;
  color: var(--text-main, #0f172a);
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6);
  max-height: 90vh;
  overflow-y: auto;
}

.modal-header {
  padding: 20px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--bg-input, #f8fafc);
}

.close-btn {
  font-size: 24px;
  cursor: pointer;
  color: var(--text-muted, #64748b);
  transition: 0.2s;
}

.close-btn:hover {
  color: var(--accent, #dc2626);
}

.modal-body {
  display: flex;
  flex-wrap: wrap;
  padding: 25px;
  gap: 25px;
}

/* GALERÍA DE IMÁGENES CON ZOOM */
.modal-gallery {
  flex: 1;
  min-width: 280px;
  max-width: 350px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-self: flex-start;
}

.main-img-box {
  width: 100%;
  height: 320px;
  background: var(--bg-input, #f8fafc);
  border-radius: 8px;
  padding: 10px;
  border: 1px solid var(--border, #e2e8f0);
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  overflow: hidden;
  cursor: zoom-in;
  position: relative;
}

.modal-img-main {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  pointer-events: none;
  will-change: transform, transform-origin;
}

.thumbnails-row {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.thumb-card {
  width: 65px;
  height: 65px;
  background: var(--bg-input, #f8fafc);
  border-radius: 6px;
  border: 2px solid var(--border, #e2e8f0);
  cursor: pointer;
  position: relative;
  flex-shrink: 0;
  overflow: hidden;
  padding: 4px;
  box-sizing: border-box;
  transition: all 0.2s ease;
}

.thumb-card:hover {
  border-color: var(--text-muted, #94a3b8);
}

.thumb-card.active {
  border-color: var(--accent, #dc2626);
  box-shadow: 0 0 8px rgba(220, 38, 38, 0.4);
}

.thumb-card img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.thumb-label {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  background: rgba(0, 0, 0, 0.75);
  color: #fff;
  font-size: 8px;
  text-align: center;
  padding: 1px 0;
  font-weight: bold;
}

/* INFORMACIÓN Y DETALLES */
.modal-info {
  flex: 1.5;
  min-width: 300px;
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.producto-titulo {
  font-size: 24px;
  font-weight: 800;
  margin: 0;
  color: var(--text-main, #0f172a);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.desc-text {
  color: var(--text-muted, #475569);
  font-size: 14px;
  line-height: 1.6;
  margin: 0;
  background: var(--bg-input, #f1f5f9);
  padding: 12px;
  border-radius: 6px;
  border: 1px solid var(--border, #e2e8f0);
}

/* CUADRÍCULA DE ESPECIFICACIONES */
.specs-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
  background: var(--bg-input, #f8fafc);
  padding: 15px;
  border-radius: 6px;
  border: 1px solid var(--border, #e2e8f0);
  max-height: 250px;
  overflow-y: auto;
}

.spec-item {
  font-size: 13px;
  line-height: 1.4;
  border-bottom: 1px dashed var(--border, #e2e8f0);
  padding-bottom: 5px;
  color: var(--text-main, #0f172a);
}

.spec-item:last-child {
  border-bottom: none;
}

.spec-item strong {
  color: var(--accent, #dc2626);
  display: block;
  font-size: 11px;
  text-transform: uppercase;
  margin-bottom: 2px;
  font-weight: 800;
}

.price-text {
  font-weight: bold;
  color: var(--text-main, #0f172a);
}

.modal-actions {
  margin-top: 15px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.btn-primary {
  background: var(--accent, #dc2626);
  color: #ffffff !important;
  border: none;
  padding: 14px;
  border-radius: 6px;
  font-weight: bold;
  cursor: pointer;
  transition: background 0.2s ease, box-shadow 0.2s ease;
  font-size: 13px;
  letter-spacing: 0.5px;
}

.btn-primary:hover:not(:disabled) {
  background: var(--accent-hover, #b91c1c) !important;
  color: #ffffff !important;
  box-shadow: 0 4px 12px rgba(220, 38, 38, 0.4);
}

.btn-primary:disabled {
  background: #64748b !important;
  cursor: not-allowed;
  box-shadow: none;
}

.btn-secondary {
  background: var(--bg-input, #f1f5f9);
  color: var(--text-main, #0f172a);
  border: 1px solid var(--border, #e2e8f0);
  padding: 12px;
  border-radius: 6px;
  font-weight: bold;
  cursor: pointer;
  transition: 0.2s;
  font-size: 13px;
}

.btn-secondary:hover {
  background: var(--border, #cbd5e1);
}

.btn-refacciones {
  background: #d97706;
  color: #ffffff;
  border: none;
  padding: 14px;
  border-radius: 6px;
  font-weight: bold;
  cursor: pointer;
  transition: 0.2s;
  font-size: 13px;
  letter-spacing: 0.5px;
}

.btn-refacciones:hover {
  background: #b45309;
  box-shadow: 0 4px 12px rgba(217, 119, 6, 0.3);
}

/* ===================================================
   SOPORTE COMPLETO PARA MODO OSCURO (DATA-THEME="DARK")
   =================================================== */
:global([data-theme='dark']) .modal-content {
  background-color: #161b22;
  border-color: #30363d;
  color: #f0f6fc;
}

:global([data-theme='dark']) .modal-header {
  background-color: #0d1117;
  border-bottom-color: #30363d;
}

:global([data-theme='dark']) .main-img-box,
:global([data-theme='dark']) .thumb-card {
  background-color: #0d1117;
  border-color: #30363d;
}

:global([data-theme='dark']) .producto-titulo,
:global([data-theme='dark']) .price-text,
:global([data-theme='dark']) .spec-item {
  color: #f0f6fc;
}

:global([data-theme='dark']) .desc-text,
:global([data-theme='dark']) .specs-grid {
  background-color: #0d1117;
  border-color: #30363d;
  color: #c9d1d9;
}

:global([data-theme='dark']) .btn-secondary {
  background-color: #21262d;
  color: #c9d1d9;
  border-color: #30363d;
}

:global([data-theme='dark']) .btn-secondary:hover {
  background-color: #30363d;
  color: #ffffff;
}

.btn-asistente {
  width: 100%;
  padding: 12px;
  border-radius: 8px;
  border: 1px dashed var(--accent, #d32f2f);
  background: transparent;
  color: var(--accent, #d32f2f);
  font-weight: 800;
  letter-spacing: 0.5px;
  cursor: pointer;
}
.btn-asistente:hover {
  background: var(--accent, #d32f2f);
  color: #fff;
}
</style>
