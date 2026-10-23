<template>
  <div class="cart-panel-overlay" :class="{ open: uiStore.isCartOpen }" @click="uiStore.closeAll">
    <aside class="cart-panel" :class="{ open: uiStore.isCartOpen }" @click.stop>
      <div class="cart-header">
        <h3>🛒 Carrito de Cotización</h3>
        <button class="btn-close" @click="uiStore.closeAll">✕</button>
      </div>

      <div class="cart-body">
        <div v-if="cartStore.items.length === 0" class="empty-cart">El carrito está vacío.</div>

        <div v-else class="cart-items-list">
          <div v-for="item in itemsConDetalle" :key="item.id" class="cart-item">
            <img :src="item.imagen" :alt="item.nombre" class="item-img" />

            <div class="item-info">
              <h4 class="item-title">{{ item.nombre }}</h4>
              <p class="item-price">${{ item.precio.toFixed(2) }} USD</p>
              <p class="item-price-mxn">~ ${{ (item.precio * TIPO_CAMBIO_MXN).toFixed(2) }} MXN</p>

              <div class="item-qty-row">
                <label>Cant:</label>
                <input
                  type="number"
                  min="1"
                  :max="item.stock"
                  :value="item.cant"
                  @input="
                    (e) =>
                      validarYActualizarCantidad(
                        item.id,
                        Number((e.target as HTMLInputElement).value),
                        item.stock,
                      )
                  "
                  class="qty-input"
                />
                <span class="stock-badge" :class="{ 'no-stock': item.stock <= 0 }">
                  (Stock: {{ item.stock }})
                </span>
              </div>
            </div>

            <button
              class="btn-remove"
              @click="cartStore.quitarProducto(item.id)"
              title="Eliminar producto"
            >
              ✕
            </button>
          </div>
        </div>
      </div>

      <div class="cart-footer">
        <div class="summary-box">
          <div class="summary-row text-muted">
            <span>Base USD:</span>
            <span>${{ subtotalUSD.toFixed(2) }} USD</span>
          </div>
          <div class="summary-row">
            <span>Subtotal (sin IVA):</span>
            <span>${{ subtotalMXN.toFixed(2) }} MXN</span>
          </div>
          <div class="summary-row text-muted">
            <span>IVA (16%):</span>
            <span>${{ ivaMXN.toFixed(2) }} MXN</span>
          </div>
          <div class="summary-row total-row">
            <span>Total (con IVA):</span>
            <span class="total-price">${{ totalMXN.toFixed(2) }} MXN</span>
          </div>
        </div>

        <p class="disclaimer">
          🔒 Tipo de cambio aplicado: $1 USD = ${{ TIPO_CAMBIO_MXN.toFixed(2) }} MXN
        </p>

        <p v-if="!authStore.usuarioActual" class="auth-warning">
          Debes iniciar sesión para procesar la orden.
        </p>

        <button
          @click="procesarCompra"
          class="checkout-btn"
          :disabled="cartStore.items.length === 0 || procesando || !authStore.usuarioActual"
        >
          {{ procesando ? 'Procesando...' : 'PROCESAR ORDEN' }}
        </button>
      </div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useCartStore } from '@/stores/cart'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { fetchProductos, type Producto } from '@/api/inventory'

const router = useRouter()
const cartStore = useCartStore()
const authStore = useAuthStore()
const uiStore = useUiStore()

const procesando = ref(false)
const productosDetalle = ref<Producto[]>([])

const TIPO_CAMBIO_MXN = 20.0
const TASA_IVA = 0.16

type ProductoExtendido = Producto & {
  ID?: string | number
  SKU?: string | number
  'no. De parte'?: string | number
  NO_DE_PARTE?: string | number
  Stock?: string | number
  Imagen_URL?: string
  Imagen?: string
  imagen?: string
  IMAGEN?: string
  Foto?: string
  URL?: string
  url?: string
  Producto?: string
  Descripcion?: string
  descripcion?: string
}

const cargarCatalogo = async () => {
  try {
    const data = await fetchProductos()
    productosDetalle.value = data
  } catch (error: unknown) {
    console.error('Error cargando detalles en carrito:', error)
  }
}

onMounted(() => {
  cargarCatalogo()
})

watch(
  () => uiStore.isCartOpen,
  (isOpen) => {
    if (isOpen) {
      cargarCatalogo()
    }
  },
)

const itemsConDetalle = computed(() => {
  return cartStore.items.map((item) => {
    const targetId = String(item.id || '')
      .trim()
      .toLowerCase()

    const prod = productosDetalle.value.find((p) => {
      const pExt = p as ProductoExtendido
      const pId = String(pExt.id || '')
        .trim()
        .toLowerCase()
      const pID = String(pExt.ID || '')
        .trim()
        .toLowerCase()
      const pSku = String(pExt.SKU || pExt['no. De parte'] || pExt.NO_DE_PARTE || '')
        .trim()
        .toLowerCase()
      const pNombre = String(pExt.Producto || pExt.Descripcion || pExt.descripcion || '')
        .trim()
        .toLowerCase()

      return (
        (pId && pId === targetId) ||
        (pID && pID === targetId) ||
        (pSku && pSku === targetId) ||
        (pNombre && pNombre === targetId)
      )
    })

    const pExt = prod as ProductoExtendido | undefined

    const precioNumerico =
      typeof pExt?.Precio === 'number' ? pExt.Precio : parseFloat(String(pExt?.Precio || 0)) || 0

    const stockNumerico =
      typeof pExt?.Stock === 'number' ? pExt.Stock : parseInt(String(pExt?.Stock || 0), 10) || 0

    const nombreProducto =
      pExt?.Producto ||
      pExt?.Descripcion ||
      pExt?.descripcion ||
      (item.id !== 'undefined' ? item.id : 'Producto sin título')

    const imgUrl =
      pExt?.Imagen_URL ||
      pExt?.Imagen ||
      pExt?.imagen ||
      pExt?.IMAGEN ||
      pExt?.Foto ||
      pExt?.URL ||
      pExt?.url ||
      'https://via.placeholder.com/60'

    return {
      id: item.id,
      cant: item.cant,
      nombre: nombreProducto,
      precio: precioNumerico,
      stock: stockNumerico,
      imagen: imgUrl,
    }
  })
})

const validarYActualizarCantidad = (id: string, nuevaCant: number, stockMaximo: number) => {
  if (isNaN(nuevaCant) || nuevaCant < 1) {
    cartStore.actualizarCantidad(id, 1)
    return
  }

  if (stockMaximo > 0 && nuevaCant > stockMaximo) {
    cartStore.actualizarCantidad(id, stockMaximo)
    return
  }

  cartStore.actualizarCantidad(id, nuevaCant)
}

const subtotalUSD = computed(() => {
  return itemsConDetalle.value.reduce((acc, item) => acc + item.precio * item.cant, 0)
})

const subtotalMXN = computed(() => {
  return subtotalUSD.value * TIPO_CAMBIO_MXN
})

const ivaMXN = computed(() => {
  return subtotalMXN.value * TASA_IVA
})

const totalMXN = computed(() => {
  return subtotalMXN.value + ivaMXN.value
})

const procesarCompra = () => {
  uiStore.closeAll()
  router.push('/checkout')
}
</script>

<style scoped>
.cart-panel-overlay {
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

.cart-panel-overlay.open {
  opacity: 1;
  visibility: visible;
}

.cart-panel {
  position: fixed;
  top: 0;
  right: -420px;
  width: 100%;
  max-width: 400px;
  height: 100vh;
  background: var(--bg-panel, #18181b);
  border-left: 1px solid var(--border, #27272a);
  color: var(--text-main, #ffffff);
  display: flex;
  flex-direction: column;
  transition: right 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: -10px 0 30px rgba(0, 0, 0, 0.5);
}

.cart-panel.open {
  right: 0;
}

.cart-header {
  padding: 20px;
  border-bottom: 1px solid var(--border, #27272a);
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.cart-header h3 {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 800;
}

.btn-close {
  background: transparent;
  border: none;
  color: var(--text-muted, #a1a1aa);
  font-size: 1.2rem;
  cursor: pointer;
  transition: color 0.2s;
}

.btn-close:hover {
  color: #ff0000;
}

.cart-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.empty-cart {
  text-align: center;
  color: var(--text-muted, #a1a1aa);
  margin-top: 50px;
  font-style: italic;
}

.cart-items-list {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.cart-item {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--bg-input, #09090b);
  border: 1px solid var(--border, #27272a);
  padding: 12px;
  border-radius: 8px;
  position: relative;
}

.item-img {
  width: 55px;
  height: 55px;
  object-fit: cover;
  border-radius: 6px;
  background: #ffffff;
}

.item-info {
  flex: 1;
}

.item-title {
  margin: 0 0 4px 0;
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--text-main, #ffffff);
  line-height: 1.2;
}

.item-price {
  margin: 0;
  color: #ff0000;
  font-weight: 800;
  font-size: 0.85rem;
}

.item-price-mxn {
  margin: 0 0 6px 0;
  color: var(--text-muted, #a1a1aa);
  font-size: 0.75rem;
}

.item-qty-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8rem;
  color: var(--text-muted, #a1a1aa);
}

.qty-input {
  width: 55px;
  background: var(--bg-panel, #18181b);
  border: 1px solid var(--border, #27272a);
  color: var(--text-main, #ffffff);
  border-radius: 4px;
  padding: 2px 6px;
  text-align: center;
  outline: none;
}

.stock-badge {
  font-size: 0.72rem;
  color: var(--text-muted, #a1a1aa);
  font-weight: 600;
}

.stock-badge.no-stock {
  color: #ef4444;
}

.btn-remove {
  background: transparent;
  border: none;
  color: #ef4444;
  cursor: pointer;
  font-size: 1rem;
  padding: 4px 8px;
}

.btn-remove:hover {
  color: #ff0000;
}

.cart-footer {
  padding: 20px;
  border-top: 1px solid var(--border, #27272a);
  background: var(--bg-panel, #18181b);
}

.summary-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: var(--bg-input, #09090b);
  padding: 12px;
  border-radius: 8px;
  border: 1px solid var(--border, #27272a);
  margin-bottom: 10px;
}

.summary-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.9rem;
  font-weight: 600;
}

.summary-row.text-muted {
  font-size: 0.8rem;
  color: var(--text-muted, #a1a1aa);
}

.summary-row.total-row {
  font-size: 1.05rem;
  font-weight: 800;
  border-top: 1px dashed var(--border, #27272a);
  padding-top: 6px;
  margin-top: 4px;
}

.total-price {
  color: #ff0000;
  font-size: 1.15rem;
}

.disclaimer {
  font-size: 0.72rem;
  color: var(--text-muted, #a1a1aa);
  text-align: center;
  margin-bottom: 12px;
}

.auth-warning {
  color: #ff4444;
  font-size: 0.8rem;
  font-weight: bold;
  text-align: center;
  margin-bottom: 10px;
}

.checkout-btn {
  width: 100%;
  background: #ff0000;
  color: #ffffff;
  border: none;
  padding: 14px;
  border-radius: 8px;
  font-weight: 900;
  font-size: 0.95rem;
  cursor: pointer;
  transition: background 0.2s;
  letter-spacing: 0.5px;
}

.checkout-btn:hover:not(:disabled) {
  background: #cc0000;
}

.checkout-btn:disabled {
  background: #27272a;
  color: #71717a;
  cursor: not-allowed;
}
</style>
