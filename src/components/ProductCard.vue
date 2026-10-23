<template>
  <div class="product-card" @click="marketStore.openModal(producto)">
    <button
      class="fav-btn"
      :class="{ 'is-active': esFavorito }"
      @click.stop="toggleFavorito"
      title="Agregar a favoritos"
    >
      <svg
        viewBox="0 0 24 24"
        width="18"
        height="18"
        :fill="esFavorito ? '#ff0000' : 'none'"
        :stroke="esFavorito ? '#ff0000' : '#64748b'"
        stroke-width="2"
      >
        <path
          d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"
        />
      </svg>
    </button>

    <div class="img-container">
      <img v-if="imagenUrl" :src="imagenUrl" :alt="nombreProducto" @error="handleImageError" />
      <div v-else class="image-fallback">
        <span>SKU: {{ skuProducto }}</span>
      </div>
    </div>

    <div class="card-body">
      <span class="category">{{ categoriaProducto }}</span>
      <h3 class="title">{{ nombreProducto }}</h3>
      <p class="sku">SKU: {{ skuProducto }}</p>

      <div class="card-footer">
        <span class="price">${{ precioFormatted }} USD</span>
        <button class="add-btn" :disabled="stockNumerico <= 0" @click.stop="handleAgregar">
          {{ stockNumerico <= 0 ? 'Agotado' : 'Añadir' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { Producto } from '@/api/inventory'
import { useCartStore } from '@/stores/cart'
import { useMarketStore } from '@/stores/market'
import { useFavoritesStore } from '@/stores/favorites'

const props = defineProps<{
  producto: Producto
}>()

const cartStore = useCartStore()
const marketStore = useMarketStore()
const favoritesStore = useFavoritesStore()

const imageError = ref(false)

const pExt = computed(() => props.producto as Record<string, unknown>)

const skuProducto = computed(() => {
  return String(
    pExt.value.id ||
      pExt.value.ID ||
      pExt.value.SKU ||
      pExt.value['no. De parte'] ||
      pExt.value.NO_DE_PARTE ||
      '',
  ).trim()
})

const esFavorito = computed(() => {
  const currentSku = skuProducto.value.toLowerCase()
  if (!currentSku) return false

  return favoritesStore.favoritos.some((f) => {
    const fExt = f as Record<string, unknown>
    const fSku = String(
      fExt.id || fExt.ID || fExt.SKU || fExt['no. De parte'] || fExt.NO_DE_PARTE || '',
    )
      .trim()
      .toLowerCase()
    return fSku === currentSku
  })
})

const stockNumerico = computed(() => {
  const val = pExt.value.Stock || pExt.value.stock
  return typeof val === 'number' ? val : parseInt(String(val || 0), 10) || 0
})

const nombreProducto = computed(() => {
  return String(
    pExt.value.Producto ||
      pExt.value['Producto '] ||
      pExt.value.Descripcion ||
      pExt.value.descripcion ||
      'Producto sin título',
  )
})

const categoriaProducto = computed(() => {
  return String(pExt.value.Categoria || pExt.value.CATEGORIA || 'GENERAL').toUpperCase()
})

// Evaluación exhaustiva de todas las llaves posibles de imagen
const imagenUrl = computed(() => {
  if (imageError.value) return ''
  const img =
    pExt.value.Imagen_Front_URL ||
    pExt.value.Photo_front_url ||
    pExt.value.Photo_front ||
    pExt.value.Imagen_Front ||
    pExt.value.Imagen_URL ||
    pExt.value['Imagen URL'] ||
    pExt.value.Imagen ||
    pExt.value.imagen ||
    pExt.value.IMAGEN ||
    pExt.value.Foto ||
    pExt.value.URL ||
    pExt.value.url
  return typeof img === 'string' && img.trim() !== '' ? img.trim() : ''
})

const precioFormatted = computed(() => {
  const p = pExt.value.Precio || pExt.value.precio
  const num = typeof p === 'number' ? p : parseFloat(String(p || 0)) || 0
  return num.toFixed(2)
})

const toggleFavorito = async () => {
  await favoritesStore.toggleFavorito(props.producto)
}

const handleImageError = () => {
  imageError.value = true
}

const handleAgregar = () => {
  if (stockNumerico.value > 0 && skuProducto.value) {
    cartStore.agregarProducto(skuProducto.value, stockNumerico.value, 1)
  }
}
</script>

<style scoped>
.product-card {
  position: relative;
  background: var(--bg-panel, #ffffff);
  border-radius: 12px;
  padding: 1rem;
  border: 1px solid var(--border, #e2e8f0);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  cursor: pointer;
  transition:
    transform 0.2s,
    box-shadow 0.2s,
    background-color 0.2s;
}

.product-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.08);
}

.fav-btn {
  position: absolute;
  top: 12px;
  right: 12px;
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid rgba(226, 232, 240, 0.8);
  border-radius: 50%;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 2;
}

.fav-btn:hover {
  transform: scale(1.08);
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.1);
}

.fav-btn.is-active {
  background: rgba(255, 255, 255, 0.95);
  border-color: rgba(255, 0, 0, 0.25);
  box-shadow: 0 0 16px rgba(255, 0, 0, 0.35);
}

.img-container {
  height: 150px;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 0.8rem;
}

.img-container img {
  max-height: 100%;
  max-width: 100%;
  object-fit: contain;
}

.image-fallback {
  width: 100%;
  height: 100%;
  background-color: var(--bg-input, #f8fafc);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted, #94a3b8);
  font-size: 0.8rem;
  font-weight: 600;
}

.category {
  font-size: 0.75rem;
  font-weight: 800;
  color: #ff0000;
}

.title {
  font-size: 0.9rem;
  font-weight: 800;
  color: var(--text-main, #0f172a);
  margin: 0.3rem 0;
  text-transform: uppercase;
}

.sku {
  font-size: 0.8rem;
  color: var(--text-muted, #64748b);
  margin-bottom: 0.8rem;
}

.card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: auto;
}

.price {
  font-size: 1.1rem;
  font-weight: 900;
  color: #ff0000;
}

.add-btn {
  background: var(--bg-panel, #ffffff);
  color: var(--text-main, #0f172a);
  border: 1px solid var(--border, #e2e8f0);
  padding: 0.5rem 1rem;
  border-radius: 999px;
  font-weight: 800;
  font-size: 0.85rem;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}

.add-btn:hover:not(:disabled) {
  background: #ff0000;
  color: #ffffff;
  border-color: #ff0000;
  box-shadow: 0 4px 10px rgba(255, 0, 0, 0.25);
}

.add-btn:disabled {
  background: var(--bg-input, #f1f5f9);
  color: var(--text-muted, #94a3b8);
  border-color: var(--border, #e2e8f0);
  cursor: not-allowed;
  box-shadow: none;
}

/* MODO OSCURO */
:global([data-theme='dark']) .product-card {
  background-color: #161b22;
  border-color: #30363d;
}

:global([data-theme='dark']) .product-card:hover {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
}

:global([data-theme='dark']) .title {
  color: #f0f6fc;
}

:global([data-theme='dark']) .sku {
  color: #8b949e;
}

:global([data-theme='dark']) .image-fallback {
  background-color: #0d1117;
  color: #8b949e;
}

:global([data-theme='dark']) .add-btn {
  background-color: #21262d;
  color: #c9d1d9;
  border-color: #30363d;
}

:global([data-theme='dark']) .add-btn:hover:not(:disabled) {
  background-color: #ff0000;
  color: #ffffff;
  border-color: #ff0000;
}

:global([data-theme='dark']) .add-btn:disabled {
  background-color: #161b22;
  color: #484f58;
  border-color: #21262d;
}

:global([data-theme='dark']) .fav-btn {
  background: rgba(22, 27, 34, 0.85);
  border-color: #30363d;
}

:global([data-theme='dark']) .fav-btn:hover {
  background: rgba(22, 27, 34, 0.95);
}

:global([data-theme='dark']) .fav-btn svg {
  stroke: #8b949e;
}

:global([data-theme='dark']) .fav-btn.is-active svg {
  stroke: #ff0000;
}
</style>
