<template>
  <div class="checkout-container">
    <NavBar />

    <main class="checkout-content">
      <button class="btn-back" @click="$router.push('/catalogo')">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2.5"
          stroke-linecap="round"
          stroke-linejoin="round"
          class="back-icon"
        >
          <line x1="19" y1="12" x2="5" y2="12"></line>
          <polyline points="12 19 5 12 12 5"></polyline>
        </svg>
        Volver al catálogo
      </button>

      <!-- PANTALLA DE PROCESANDO PEDIDO (ÉXITO) -->
      <div v-if="pagoExitoso" class="status-card success-card">
        <div class="status-icon">⏳</div>
        <h2>¡Pedido Registrado con Éxito!</h2>
        <p>
          Tu orden <strong>#{{ idOrden }}</strong> ha sido creada.
          <span v-if="metodoPago === 'transferencia'">
            Tu comprobante fue enviado para su revisión manual.</span
          >
        </p>
        <p class="status-desc">
          Te notificaremos por correo electrónico en cuanto tu estado sea actualizado.
        </p>
        <button class="btn-primary" @click="$router.push('/perfil')">
          Ver estado en mi perfil
        </button>
      </div>

      <!-- PANTALLA DE ERROR -->
      <div v-else-if="errorMensaje && !procesando" class="status-card error-card">
        <div class="status-icon">❌</div>
        <h2>No se pudo procesar la orden</h2>
        <p class="status-desc">{{ errorMensaje }}</p>
        <button class="btn-secondary" @click="errorMensaje = ''">Intentar de nuevo</button>
      </div>

      <!-- VISTA PRINCIPAL DEL CHECKOUT -->
      <div v-else class="checkout-grid">
        <section class="payment-section">
          <h2>Finalizar Pedido</h2>
          <p class="mp-subtitle">
            Selecciona tu método de pago preferido para registrar la compra.
          </p>

          <div class="user-info-box">
            <p>
              <strong>Cliente:</strong>
              {{ authStore.perfil?.nombre || authStore.usuarioActual?.email || 'Usuario' }}
            </p>
            <p><strong>Correo:</strong> {{ authStore.usuarioActual?.email }}</p>
          </div>

          <!-- SELECTOR DE MÉTODOS DE PAGO -->
          <div class="payment-methods-selector">
            <label class="section-label">Método de Pago:</label>
            <div class="methods-grid">
              <!-- OPCIÓN 1: TRANSFERENCIA MANUAL -->
              <div
                :class="['method-card', { active: metodoPago === 'transferencia' }]"
                @click="metodoPago = 'transferencia'"
              >
                <span class="method-icon">🏦</span>
                <div class="method-details">
                  <strong>Transferencia / Depósito</strong>
                  <small>Revisión manual con comprobante</small>
                </div>
              </div>

              <!-- OPCIÓN 2: TARJETA(STRIPE) -->
              <div
                :class="['method-card', { active: metodoPago === 'stripe' }]"
                @click="metodoPago = 'stripe'"
              >
                <span class="method-icon">💳</span>
                <div class="method-details">
                  <strong>Tarjeta de Crédito / Débito (Stripe)</strong>
                  <small>Pasarela de pago segura de Stripe</small>
                </div>
              </div>
            </div>
          </div>

          <!-- PANELES SEGÚN MÉTODO -->

          <!-- 1. TRANSFERENCIA -->
          <div v-if="metodoPago === 'transferencia'" class="method-panel">
            <div class="bank-details-box">
              <h4>Datos Bancarios para Transferencia (SPEI):</h4>
              <div class="bank-row"><span>Banco:</span> <strong>BBVA México</strong></div>
              <div class="bank-row">
                <span>Beneficiario:</span> <strong>Relant Fluid Dosing S.A. de C.V.</strong>
              </div>
              <div class="bank-row"><span>CLABE:</span> <strong>012 320 00123456789 0</strong></div>
              <div class="bank-row"><span>RFC:</span> <strong>RFD180512XYZ</strong></div>
            </div>

            <div class="upload-section">
              <label class="upload-label"> 📎 Adjuntar Comprobante de Pago (Imagen): </label>
              <input type="file" accept="image/*" @change="handleFileUpload" class="file-input" />
              <div v-if="imagenPruebaBase64" class="preview-box">
                <img :src="imagenPruebaBase64" alt="Vista previa del comprobante" />
                <button class="btn-remove-img" @click="imagenPruebaBase64 = ''">
                  ✕ Eliminar imagen
                </button>
              </div>
            </div>
          </div>

          <!-- 2. STRIPE -->
          <div v-else-if="metodoPago === 'stripe'" class="method-panel">
            <div class="stripe-info-box">
              <p>
                🔒 Serás redirigido a la pasarela segura de <strong>Stripe</strong> donde podrás
                ingresar tu tarjeta de Crédito/Débito. Una vez aprobado el pago, tu orden será
                registrada automáticamente y recibirás un correo de confirmación.
              </p>
            </div>
          </div>

          <button
            @click="procesarOrdenLocal"
            :disabled="
              procesando ||
              itemsConDetalle.length === 0 ||
              (metodoPago === 'transferencia' && !imagenPruebaBase64)
            "
            class="btn-confirmar"
          >
            {{
              procesando
                ? 'Procesando...'
                : metodoPago === 'transferencia' && !imagenPruebaBase64
                  ? 'ADJUNTA TU COMPROBANTE PARA CONTINUAR'
                  : metodoPago === 'stripe'
                    ? 'PAGAR CON STRIPE 🔒'
                    : 'CONFIRMAR Y GENERAR ORDEN'
            }}
          </button>
        </section>

        <!-- RESUMEN DEL PEDIDO -->
        <aside class="summary-section">
          <h3>Resumen del Pedido</h3>
          <div v-if="itemsConDetalle.length === 0" class="empty-summary">
            No hay productos en el carrito.
          </div>
          <div v-else class="items-list">
            <div v-for="item in itemsConDetalle" :key="item.id" class="summary-item">
              <div class="item-img-wrapper">
                <img :src="item.imagen" :alt="item.nombre" class="item-img" />
              </div>
              <div class="item-info">
                <h4 class="item-title">{{ item.nombre }}</h4>
                <div class="item-meta">
                  <span class="item-qty">Cant: {{ item.cant }}</span>
                  <span class="item-price">${{ item.precio.toFixed(2) }} USD</span>
                </div>
              </div>
            </div>
          </div>
          <div class="order-divider"></div>
          <div class="summary-total-row">
            <span>Total Estimado:</span>
            <span class="total-amount">${{ totalPrecio.toFixed(2) }} USD</span>
          </div>
        </aside>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useCartStore } from '@/stores/cart'
import { useAuthStore } from '@/stores/auth'
import { fetchProductos, type Producto } from '@/api/inventory'
import { db } from '@/firebase'
import { ref as dbRef, set, runTransaction, get } from 'firebase/database'
import { getAuth } from 'firebase/auth'
import NavBar from '@/components/NavBar.vue'

type ProductoExtendido = Producto & {
  ID?: string | number
  SKU?: string | number
  'no. De parte'?: string | number
  NO_DE_PARTE?: string | number
  Producto?: string
  Precio?: string | number
  Imagen_URL?: string
  Stock?: string | number
}

interface ItemOrdenBase {
  id: string
  cant?: number
  cantidad?: number
  nombre?: string
  precio?: number
}

const route = useRoute()
const cartStore = useCartStore()
const authStore = useAuthStore()

const procesando = ref(false)
const pagoExitoso = ref(false)
const idOrden = ref('')
const errorMensaje = ref('')
const productosDetalle = ref<Producto[]>([])
const metodoPago = ref<'transferencia' | 'stripe'>('transferencia')
const imagenPruebaBase64 = ref<string>('')

const SCRIPT_URL: string =
  'https://script.google.com/macros/s/AKfycbx8VdglYn-7wYotj6WCK3t3bP8Is6-X_B3RSBdlP1ZDj-VFNw27o_63WmQZccVsXizu/exec'

onMounted(async () => {
  productosDetalle.value = await fetchProductos()

  // Retorno desde Stripe Checkout
  if (route.query.stripe_success === 'true') {
    await finalizarOrdenStripeExitosa()
  } else if (route.query.stripe_cancel === 'true') {
    localStorage.removeItem('pending_stripe_order')
    errorMensaje.value =
      'El pago con Stripe fue cancelado. No se realizó ningún cargo ni se generó el pedido.'
  }
})

// Registra la orden únicamente tras confirmación real de Stripe
const finalizarOrdenStripeExitosa = async () => {
  const pendingRaw = localStorage.getItem('pending_stripe_order')
  if (!pendingRaw) return

  procesando.value = true
  try {
    const pendingOrden = JSON.parse(pendingRaw)
    const auth = getAuth()
    const firebaseUser = auth.currentUser || authStore.usuarioActual
    const uid = firebaseUser?.uid || pendingOrden.userId

    // 1. Descontar Inventario en Firebase
    const inventarioRef = dbRef(db, 'inventario')
    await get(inventarioRef)

    await runTransaction(inventarioRef, (inventarioActual) => {
      const nuevoInventario = inventarioActual ? { ...inventarioActual } : {}
      for (const item of pendingOrden.items) {
        const key = String(item.id).trim().toLowerCase()
        const stockDisponible = typeof nuevoInventario[key] === 'number' ? nuevoInventario[key] : 20
        nuevoInventario[key] = Math.max(0, stockDisponible - item.cant)
      }
      return nuevoInventario
    })

    // 2. Guardar Orden en Firebase Realtime Database
    await set(dbRef(db, `ordenes/${uid}/${pendingOrden.id}`), pendingOrden)

    // 3. Descontar stock en Google Sheets
    descontarStockEnBackend(pendingOrden.items)

    // 4. Limpiar sesión y confirmar pedido
    localStorage.removeItem('pending_stripe_order')
    cartStore.vaciarCarrito()
    idOrden.value = pendingOrden.id
    metodoPago.value = 'stripe'
    pagoExitoso.value = true
  } catch (e) {
    console.error('Error guardando la orden de Stripe:', e)
    errorMensaje.value = 'Ocurrió un error al registrar tu pago aprobado.'
  } finally {
    procesando.value = false
  }
}

const handleFileUpload = (e: Event) => {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (file) {
    const reader = new FileReader()
    reader.onload = (evt) => {
      imagenPruebaBase64.value = evt.target?.result as string
    }
    reader.readAsDataURL(file)
  }
}

const itemsConDetalle = computed(() => {
  if (!cartStore.items) return []
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
      const pSKU = String(pExt.SKU || '')
        .trim()
        .toLowerCase()
      const pParte = String(pExt['no. De parte'] || pExt.NO_DE_PARTE || '')
        .trim()
        .toLowerCase()
      return pId === targetId || pID === targetId || pSKU === targetId || pParte === targetId
    })

    const pExtFinal = (prod || {}) as ProductoExtendido
    const stockRaw = pExtFinal.Stock ?? 0
    const stockNum =
      typeof stockRaw === 'number' ? stockRaw : parseInt(String(stockRaw || 0), 10) || 0

    return {
      id: item.id,
      cant: item.cant || 1,
      nombre: pExtFinal.Producto || item.id,
      precio: parseFloat(String(pExtFinal.Precio || 0)) || 0,
      imagen: pExtFinal.Imagen_URL || `https://via.placeholder.com/60`,
      stockReal: stockNum,
    }
  })
})

const totalPrecio = computed(() => {
  return itemsConDetalle.value.reduce((acc, item) => acc + item.precio * item.cant, 0)
})

const procesarOrdenLocal = async () => {
  const auth = getAuth()
  const firebaseUser = auth.currentUser || authStore.usuarioActual

  if (!firebaseUser || !firebaseUser.uid) {
    errorMensaje.value = 'La sesión de usuario no está activa. Por favor, inicia sesión de nuevo.'
    return
  }

  procesando.value = true
  errorMensaje.value = ''

  const uid = firebaseUser.uid
  const generatedId = 'ORD-' + Date.now()

  // --- OPCIÓN A: COBRO VÍA STRIPE ---
  if (metodoPago.value === 'stripe') {
    const ordenStripePendiente = {
      id: generatedId,
      userId: uid,
      fecha: new Date().toLocaleDateString('es-MX'),
      total: Number(totalPrecio.value) || 0,
      estado: 'En preparacion',
      metodoPago: 'stripe',
      items: itemsConDetalle.value.map((i) => ({
        id: String(i.id || ''),
        cant: Number(i.cant) || 1,
        nombre: String(i.nombre || i.id || 'Producto'),
        precio: Number(i.precio) || 0,
      })),
    }

    // Se guarda como borrador en localStorage en espera del pago
    localStorage.setItem('pending_stripe_order', JSON.stringify(ordenStripePendiente))

    try {
      const resp = await fetch(SCRIPT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: JSON.stringify({
          accion: 'crearStripeSession',
          ordenId: generatedId,
          email: firebaseUser.email,
          items: itemsConDetalle.value,
          successUrl: window.location.origin + '/checkout?stripe_success=true',
          cancelUrl: window.location.origin + '/checkout?stripe_cancel=true',
        }),
      })

      const resJson = await resp.json()

      if (resJson.checkoutUrl) {
        window.location.href = resJson.checkoutUrl
        return
      } else {
        throw new Error('No se pudo redirigir a la pasarela de Stripe.')
      }
    } catch (err: unknown) {
      localStorage.removeItem('pending_stripe_order')
      errorMensaje.value = err instanceof Error ? err.message : 'Error conectando con Stripe.'
      procesando.value = false
      return
    }
  }

  // --- OPCIÓN B: TRANSFERENCIA MANUAL ---
  const nuevaOrdenTransferencia = {
    id: generatedId,
    userId: uid,
    fecha: new Date().toLocaleDateString('es-MX'),
    total: Number(totalPrecio.value) || 0,
    estado: 'En proceso',
    metodoPago: 'transferencia',
    comprobante: imagenPruebaBase64.value || '',
    items: itemsConDetalle.value.map((i) => ({
      id: String(i.id || ''),
      cant: Number(i.cant) || 1,
      nombre: String(i.nombre || i.id || 'Producto'),
      precio: Number(i.precio) || 0,
    })),
  }

  try {
    const inventarioRef = dbRef(db, 'inventario')
    await get(inventarioRef)

    const transaccion = await runTransaction(inventarioRef, (inventarioActual) => {
      const nuevoInventario = inventarioActual ? { ...inventarioActual } : {}

      for (const item of itemsConDetalle.value) {
        const key = String(item.id).trim().toLowerCase()
        const stockDisponible =
          typeof nuevoInventario[key] === 'number' ? nuevoInventario[key] : item.stockReal

        if (stockDisponible < item.cant) {
          return
        }
        nuevoInventario[key] = stockDisponible - item.cant
      }

      return nuevoInventario
    })

    if (!transaccion.committed) {
      throw new Error(
        '¡Ups! Uno o más artículos de tu carrito acaban de agotarse. Por favor revisa el catálogo nuevamente.',
      )
    }

    const ordenLimpia = JSON.parse(JSON.stringify(nuevaOrdenTransferencia))
    await set(dbRef(db, `ordenes/${uid}/${generatedId}`), ordenLimpia)

    descontarStockEnBackend(nuevaOrdenTransferencia.items)

    cartStore.vaciarCarrito()
    idOrden.value = generatedId
    pagoExitoso.value = true
  } catch (error: unknown) {
    console.error('Error al generar la orden:', error)
    if (error instanceof Error) {
      errorMensaje.value = error.message
    } else {
      errorMensaje.value = 'Hubo un error al procesar la orden. Intenta nuevamente.'
    }
  } finally {
    procesando.value = false
  }
}

const descontarStockEnBackend = async (itemsList: ItemOrdenBase[]) => {
  if (!itemsList || itemsList.length === 0) return
  try {
    await fetch(SCRIPT_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain;charset=utf-8' },
      body: JSON.stringify({
        items: itemsList.map((item) => ({
          id: item.id,
          cantidad: item.cant || item.cantidad || 1,
        })),
      }),
    })
  } catch (error: unknown) {
    console.error('Error en Sheets:', error)
  }
}
</script>

<style scoped>
.checkout-container {
  min-height: 100vh;
  background-color: var(--bg-main, #f4f6f8);
  color: var(--text-main, #1c1e21);
}

.checkout-content {
  max-width: 1100px;
  margin: 0 auto;
  padding: 30px 20px 80px 20px;
}

.btn-back {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: none;
  border: none;
  color: var(--text-muted, #6a737d);
  font-size: 1rem;
  font-weight: 700;
  cursor: pointer;
  margin-bottom: 25px;
  padding: 0;
  transition:
    color 0.2s ease,
    transform 0.2s ease;
}

.btn-back:hover {
  color: var(--accent, #e52e2e);
  transform: translateX(-5px);
}

.checkout-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 30px;
  align-items: start;
}

.payment-section,
.summary-section {
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #d1d5da);
  border-radius: 12px;
  padding: 28px;
  box-shadow: 0 4px 15px rgba(0, 0, 0, 0.03);
}

.payment-section h2,
.summary-section h3 {
  font-size: 1.3rem;
  font-weight: 800;
  margin-top: 0;
  margin-bottom: 8px;
}

.mp-subtitle {
  color: var(--text-muted, #6a737d);
  font-size: 0.9rem;
  margin-bottom: 25px;
}

.user-info-box {
  background: var(--bg-input, #f8f9fa);
  padding: 15px;
  border-radius: 8px;
  border: 1px solid var(--border, #e2e8f0);
  margin-bottom: 20px;
  font-size: 0.95rem;
}

.user-info-box p {
  margin: 5px 0;
}

.payment-methods-selector {
  margin-bottom: 20px;
}

.section-label {
  display: block;
  font-weight: 800;
  font-size: 0.9rem;
  margin-bottom: 10px;
  color: var(--text-main, #0f172a);
}

.methods-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.method-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border: 2px solid var(--border, #cbd5e1);
  border-radius: 10px;
  background: var(--bg-panel, #ffffff);
  cursor: pointer;
  transition: all 0.2s ease;
}

.method-card:hover {
  border-color: var(--text-muted, #94a3b8);
}

.method-card.active {
  border-color: var(--accent, #e52e2e);
  background: rgba(229, 46, 46, 0.04);
}

.method-icon {
  font-size: 1.5rem;
}

.method-details {
  display: flex;
  flex-direction: column;
}

.method-details strong {
  font-size: 0.92rem;
  color: var(--text-main, #0f172a);
}

.method-details small {
  font-size: 0.78rem;
  color: var(--text-muted, #64748b);
}

.method-panel {
  margin-bottom: 25px;
}

.bank-details-box {
  background: var(--bg-input, #f8f9fa);
  border: 1px solid var(--border, #cbd5e1);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 15px;
}

.bank-details-box h4 {
  margin: 0 0 10px 0;
  font-size: 0.9rem;
  color: var(--accent, #e52e2e);
}

.bank-row {
  display: flex;
  justify-content: space-between;
  font-size: 0.85rem;
  margin-bottom: 6px;
  border-bottom: 1px dashed var(--border, #e2e8f0);
  padding-bottom: 4px;
}

.bank-row:last-child {
  border-bottom: none;
  margin-bottom: 0;
  padding-bottom: 0;
}

.upload-section {
  background: var(--bg-input, #f8f9fa);
  border: 2px dashed var(--border, #cbd5e1);
  border-radius: 8px;
  padding: 16px;
}

.upload-label {
  display: block;
  font-weight: 700;
  font-size: 0.88rem;
  margin-bottom: 8px;
  color: var(--text-main, #0f172a);
}

.file-input {
  width: 100%;
  font-size: 0.85rem;
}

.preview-box {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
}

.preview-box img {
  max-width: 180px;
  max-height: 120px;
  object-fit: contain;
  border-radius: 6px;
  border: 1px solid var(--border, #cbd5e1);
  background: #ffffff;
}

.btn-remove-img {
  background: none;
  border: none;
  color: #dc2626;
  font-weight: 700;
  font-size: 0.8rem;
  cursor: pointer;
  padding: 0;
}

.stripe-info-box {
  background: var(--bg-input, #f8f9fa);
  border: 1px solid var(--border, #cbd5e1);
  border-radius: 8px;
  padding: 18px;
  font-size: 0.88rem;
  color: var(--text-muted, #475569);
  text-align: center;
  line-height: 1.5;
}

.btn-confirmar {
  width: 100%;
  background: #e52e2e;
  color: #ffffff;
  border: none;
  padding: 16px;
  border-radius: 8px;
  font-weight: 800;
  font-size: 0.95rem;
  cursor: pointer;
  transition: background-color 0.2s ease;
}

.btn-confirmar:hover:not(:disabled) {
  background: #c22525;
}

.btn-confirmar:disabled {
  opacity: 0.65;
  cursor: not-allowed;
  background: #94a3b8;
}

.empty-summary {
  text-align: center;
  color: var(--text-muted, #6a737d);
  padding: 30px 0;
  font-style: italic;
}

.items-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: 380px;
  overflow-y: auto;
  padding-right: 6px;
  margin-top: 15px;
}

.summary-item {
  display: flex;
  align-items: center;
  gap: 14px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border, #eef2f5);
}

.item-img-wrapper {
  width: 65px;
  height: 65px;
  min-width: 65px;
  background: #ffffff;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 4px;
}

.item-img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.item-info {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
}

.item-title {
  font-size: 0.9rem;
  font-weight: 700;
  margin: 0 0 6px 0;
  line-height: 1.3;
}

.item-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.item-qty {
  font-size: 0.8rem;
  color: var(--text-muted, #6a737d);
  font-weight: 600;
}

.item-price {
  font-size: 0.9rem;
  font-weight: 800;
  color: var(--accent, #e52e2e);
}

.order-divider {
  border-top: 2px dashed var(--border, #d1d5da);
  margin: 20px 0;
}

.summary-total-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 1.15rem;
  font-weight: 800;
}

.total-amount {
  color: var(--accent, #e52e2e);
  font-size: 1.25rem;
  font-weight: 900;
}

.status-card {
  text-align: center;
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #d1d5da);
  padding: 50px 30px;
  border-radius: 16px;
  max-width: 520px;
  margin: 40px auto;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.05);
}

.status-icon {
  font-size: 55px;
  margin-bottom: 15px;
}

.status-card h2 {
  font-size: 1.5rem;
  margin-bottom: 10px;
}

.status-desc {
  color: var(--text-muted, #6a737d);
  font-size: 0.95rem;
  margin-bottom: 25px;
}

.btn-primary,
.btn-secondary {
  border: none;
  padding: 14px 28px;
  border-radius: 8px;
  font-weight: 800;
  font-size: 0.95rem;
  cursor: pointer;
}

.btn-primary {
  background: var(--accent, #e52e2e);
  color: #ffffff;
}

.btn-secondary {
  background: var(--bg-input, #eef2f5);
  color: var(--text-main);
  border: 1px solid var(--border, #d1d5da);
}

@media (max-width: 850px) {
  .checkout-grid {
    grid-template-columns: 1fr;
  }
}
</style>
