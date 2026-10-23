<template>
  <div :class="esModoOscuro ? 'maquina-dark' : 'maquina-light'" class="maquina-dashboard">
    <NavBar @toggle-cart="$emit('toggle-cart')" />

    <!-- 🔐 PANTALLA 1: MODAL DE VALIDACIÓN OBLIGATORIO -->
    <div v-if="!licenciaValidada" class="licencia-overlay">
      <div class="licencia-modal-card">
        <div class="modal-lock-icon">🔐</div>
        <h2>Validación de Licencia Requerida</h2>
        <p class="modal-instruction">
          Esta máquina expendedora se encuentra en un nodo seguro. Ingrese sus credenciales de
          planta para activar la interfaz.
        </p>

        <form @submit.prevent="validarLicenciaConServidor" class="modal-form">
          <div class="form-group">
            <label>Clave de Licencia:</label>
            <input
              v-model="formClave"
              type="password"
              placeholder="Ej: CLAVE-1234"
              required
              class="modal-input"
            />
          </div>

          <div class="form-group">
            <label>Correo de Usuario:</label>
            <input
              v-model="formEmail"
              type="email"
              placeholder="nombre@empresa.com"
              required
              class="modal-input"
            />
          </div>

          <div class="form-group">
            <label>Nombre de Usuario:</label>
            <input
              v-model="formNombre"
              type="text"
              placeholder="Ej: Ing. Carlos Pérez"
              required
              class="modal-input"
            />
          </div>

          <!-- Banner de error dinámico del backend -->
          <div v-if="errorValidacion" class="error-validation-banner">❌ {{ errorValidacion }}</div>

          <button type="submit" :disabled="validando" class="btn-submit-modal">
            {{ validando ? 'CONECTANDO CON PLC...' : 'VERIFICAR Y ACTIVAR PANEL' }}
          </button>
        </form>
      </div>
    </div>

    <!-- 🚨 PANTALLA 2: CORTE AUTOMÁTICO POR FALTA DE PAGO -->
    <div v-else-if="maquinaEstaBloqueada" class="corte-pago-overlay">
      <div class="corte-card">
        <span class="corte-icon">⚠️</span>
        <h2>EQUIPO SUSPENDIDO</h2>
        <p class="corte-error-code">ERROR_CODE: RELANT_SUSPENSION_LINE_01</p>
        <div class="corte-divider"></div>
        <p class="corte-desc">
          Las operaciones de este módulo expendedor han sido congeladas remotamente debido al
          incumplimiento de pago de la licencia de software contratada.
        </p>
        <p class="corte-action">
          Por favor, comuníquese con el departamento administrativo de
          <strong>Relant S.A. de C.V.</strong> para regularizar su saldo y restablecer la corriente
          del PLC.
        </p>
      </div>
    </div>

    <!-- PANTALLA 3: INTERFAZ GENERAL DE OPERACIONES -->
    <div v-else-if="!cargandoHardware" class="dashboard-content">
      <header class="dash-header">
        <div class="header-flex-container">
          <div>
            <h1 class="dash-title">Panel de Control: Máquina Expendedora</h1>
            <p class="dash-subtitle">
              ID de Equipo: <span class="machine-id-text">{{ idMaquina }}</span> | Licencia:
              <span class="status-active">Activa</span> | Estatus:
              <span class="status-online">En Línea</span>
            </p>
          </div>

          <!-- 🔒 CANDADO MODO DUEÑO -->
          <div v-if="esAdmin" class="admin-toggle-zone">
            <button
              @click="modoDueno = !modoDueno"
              :class="modoDueno ? 'btn-admin-on' : 'btn-admin-off'"
            >
              {{ modoDueno ? '🔒 Salir Modo Dueño' : '🛠️ Entrar Modo Dueño' }}
            </button>
          </div>
        </div>
      </header>

      <!-- BANNER DE EDICIÓN HARDWARE -->
      <div v-if="modoDueno && esAdmin" class="owner-alert-banner">
        ⚡ <strong>CONSOLA DE DUEÑO ACTIVA:</strong> Cambios guardados instantáneamente en la base
        de datos centralizada de Prisma.
      </div>

      <div class="main-grid">
        <!-- Layout del Inventario -->
        <div class="left-col">
          <div class="widget">
            <h3>
              {{
                modoDueno && esAdmin
                  ? '🔧 Gestión de Ranuras (Precios y Stock)'
                  : 'Layout de Máquina (Inventario Actual)'
              }}
            </h3>

            <div class="coil-grid">
              <div
                v-for="item in inventarioMaquina"
                :key="item.id"
                :class="[
                  'coil-slot',
                  { 'out-of-stock': item.stock === 0, 'slot-admin-mode': modoDueno && esAdmin },
                ]"
                @click="!(modoDueno && esAdmin) && agregarAlPedido(item)"
              >
                <span class="slot-id">{{ item.posicion }}</span>

                <!-- Modo Cliente -->
                <template v-if="!(modoDueno && esAdmin)">
                  <span class="slot-name">{{ item.nombre }}</span>
                  <span class="slot-price">${{ item.precio.toFixed(2) }}</span>
                  <span class="slot-stock">
                    {{ item.stock === 0 ? '🚫 Sin Stock' : `Disp: ${item.stock}` }}
                  </span>
                </template>

                <!-- Modo Dueño Remoto -->
                <template v-else>
                  <span class="slot-name-admin">{{ item.nombre }}</span>

                  <div class="admin-input-group">
                    <label>Precio (USD):</label>
                    <input
                      type="number"
                      v-model.number="item.precio"
                      step="0.5"
                      @change="guardarCambioFisicoBD(item)"
                      class="input-admin-field"
                    />
                  </div>

                  <div class="admin-input-group">
                    <label>Stock Físico:</label>
                    <div class="admin-stock-adjuster">
                      <button @click="ajustarStockBoton(item, -1)" class="btn-stock-qty">-</button>
                      <span class="stock-qty-num">{{ item.stock }}</span>
                      <button @click="ajustarStockBoton(item, 1)" class="btn-stock-qty">+</button>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </div>

        <!-- Columna Derecha -->
        <div class="right-col">
          <!-- GENERAR PEDIDO -->
          <div v-if="!(modoDueno && esAdmin)" class="widget order-widget">
            <h3>Generar Pedido</h3>
            <div class="order-items">
              <div v-if="pedidoActual.length === 0" class="empty-order">
                No ha seleccionado artículos.
              </div>
              <div v-else v-for="item in pedidoActual" :key="item.posicion" class="order-item">
                <div class="order-item-info">
                  <span class="item-pos">[{{ item.posicion }}]</span> {{ item.nombre }}
                  <div class="item-subtotal-text">
                    ${{ (item.precio * item.cantidad).toFixed(2) }} USD
                  </div>
                </div>
                <div class="quantity-controls">
                  <button class="btn-qty" @click.stop="cambiarCantidad(item.posicion, -1)">
                    -
                  </button>
                  <span class="qty-number">{{ item.cantidad }}</span>
                  <button class="btn-qty" @click.stop="cambiarCantidad(item.posicion, 1)">+</button>
                </div>
              </div>
            </div>

            <div class="order-summary" v-if="pedidoActual.length > 0">
              <div class="summary-row total-row">
                <span>Total a descontar:</span>
                <span>${{ totalPedido.toFixed(2) }} USD</span>
              </div>
            </div>

            <button
              class="btn-confirm"
              :disabled="pedidoActual.length === 0 || procesando || superaPresupuesto"
              @click="procesarPedido"
            >
              {{ procesando ? estadoProceso : 'CONFIRMAR DESPACHO PLC' }}
            </button>
          </div>

          <!-- MODO OPERACIONES MASIVAS -->
          <div v-else class="widget owner-panel-widget">
            <h3>⚙️ Consola Global Remota</h3>
            <button
              @click="ejecutarOperacionMasiva('restock')"
              class="btn-owner-action btn-emerald"
              style="margin-bottom: 10px"
            >
              📦 Reabastecer Masivo Base de Datos
            </button>
            <button @click="ejecutarOperacionMasiva('reset')" class="btn-owner-action btn-amber">
              💰 Reiniciar Consumos Financieros
            </button>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="cargando-pantalla">Conectando con la base de datos central...</div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import NavBar from '@/components/NavBar.vue'

defineEmits(['toggle-cart'])

const route = useRoute()
const authStore = useAuthStore()
const idMaquina = ref((route.params.id as string) || '8de214c3-f6bf-402b-92c7-d39de7f98efc')

interface Producto {
  id: string
  posicion: string
  nombre: string
  precio: number
  stock: number
}

interface ItemPedido {
  posicion: string
  nombre: string
  precio: number
  cantidad: number
  stockMaximo: number
}

// ESTADOS PARA EL INTERCEPTOR DE LICENCIA CENTRAL
const licenciaValidada = ref(false)
const validando = ref(false)
const errorValidacion = ref('')
const formClave = ref('')
const formEmail = ref(authStore.usuarioActual?.email || '')
const formNombre = ref('')

const inventarioMaquina = ref<Producto[]>([])
const maquinaEstaBloqueada = ref(false)
const cargandoHardware = ref(true)
const modoDueno = ref(false)
const pedidoActual = ref<ItemPedido[]>([])
const presupuestoConsumido = ref(0)
const presupuestoMaximo = 4000.0
const procesando = ref(false)
const estadoProceso = ref('')
const esModoOscuro = ref(true)

const esAdmin = computed(() => {
  const usuario = authStore.usuarioActual
  if (!usuario || !('rol' in usuario) || typeof usuario.rol !== 'string') return false
  return usuario.rol.toUpperCase() === 'ADMIN'
})

// 🔑 FLUJO CORRECTO: 1. Valida licencia con Express -> 2. Si pasa, consulta hardware
const validarLicenciaConServidor = async () => {
  validando.value = true
  errorValidacion.value = ''

  try {
    const respuesta = await fetch('http://localhost:3000/api/validar-licencia', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clave: formClave.value,
        emailUsuario: formEmail.value,
        nombreUsuario: formNombre.value,
      }),
    })

    const datos = await respuesta.json()

    if (respuesta.ok && datos.success) {
      licenciaValidada.value = true
      if (datos.maquinaId) {
        idMaquina.value = datos.maquinaId
      }
      // Encendemos la comunicación física con los productos de la BD
      await consultarServidorCentral()
    } else {
      errorValidacion.value = datos.error || 'Error de autenticación.'
    }
  } catch (error) {
    console.error(error)
    errorValidacion.value =
      'No se pudo conectar con el servidor API local (Verifica que server.js esté encendido).'
  } finally {
    validando.value = false
  }
}

// 🌐 CONSULTA CENTRAL DE HARDWARE REAL
const consultarServidorCentral = async () => {
  try {
    const res = await fetch(`http://localhost:3000/api/maquinas/${idMaquina.value}`)
    if (res.ok) {
      const maquina = await res.json()
      maquinaEstaBloqueada.value = !maquina.licencia?.activa
      inventarioMaquina.value = maquina.productos
      presupuestoConsumido.value = maquina.presupuestoUso
    }
  } catch (error) {
    console.error('Fallo de comunicación con la base de datos central.', error)
  } finally {
    cargandoHardware.value = false
  }
}

// 💾 IMPACTAR CAMBIOS EN LA CONSOLA DE DUEÑO
const guardarCambioFisicoBD = async (item: Producto) => {
  try {
    await fetch(`http://localhost:3000/api/productos/${item.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ precio: item.precio, stock: item.stock }),
    })
  } catch (error) {
    console.error('Error al guardar en el backend.', error)
  }
}

const ajustarStockBoton = async (item: Producto, delta: number) => {
  item.stock = Math.max(0, item.stock + delta)
  await guardarCambioFisicoBD(item)
}

const ejecutarOperacionMasiva = async (accion: 'restock' | 'reset') => {
  try {
    const res = await fetch(`http://localhost:3000/api/maquinas/${idMaquina.value}/operaciones`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accion }),
    })
    if (res.ok) {
      const maquina = await res.json()
      inventarioMaquina.value = maquina.productos
      presupuestoConsumido.value = maquina.presupuestoUso
      alert('Operación masiva inyectada en base de datos con éxito.')
    }
  } catch (error) {
    console.error(error)
  }
}

// LÓGICA DE CLIENTE TRADICIONAL
const totalPedido = computed(() =>
  pedidoActual.value.reduce((t, i) => t + i.precio * i.cantidad, 0),
)
const presupuestoDisponible = computed(() => presupuestoMaximo - presupuestoConsumido.value)
const superaPresupuesto = computed(() => totalPedido.value > presupuestoDisponible.value)

const agregarAlPedido = (item: Producto) => {
  if (item.stock === 0) return
  const existente = pedidoActual.value.find((p) => p.posicion === item.posicion)
  if (existente) {
    if (existente.cantidad < item.stock) {
      existente.cantidad++
    } else {
      alert(`⚠️ Ranura al límite: Solo quedan ${item.stock} piezas físicas disponibles.`)
    }
  } else {
    pedidoActual.value.push({
      posicion: item.posicion,
      nombre: item.nombre,
      precio: item.precio,
      cantidad: 1,
      stockMaximo: item.stock,
    })
  }
}

// 🛡️ REPARACIÓN 1: FRENO DE STOCK MÁXIMO ACTIVADO AQUÍ
const cambiarCantidad = (posicion: string, incremento: number) => {
  const item = pedidoActual.value.find((p) => p.posicion === posicion)
  if (!item) return

  const nuevaCantidad = item.cantidad + incremento
  if (nuevaCantidad <= 0) {
    pedidoActual.value = pedidoActual.value.filter((p) => p.posicion !== posicion)
  } else if (nuevaCantidad <= item.stockMaximo) {
    // 👈 Comprobación estricta de hardware
    item.cantidad = nuevaCantidad
  } else {
    alert(
      `⚠️ Ranura al límite: No se pueden extraer más de las ${item.stockMaximo} unidades en stock real.`,
    )
  }
}

const procesarPedido = () => {
  procesando.value = true
  estadoProceso.value = 'Despachando componentes vía PLC...'
  setTimeout(async () => {
    for (const itemCart of pedidoActual.value) {
      const itemInv = inventarioMaquina.value.find((i) => i.posicion === itemCart.posicion)
      if (itemInv) {
        itemInv.stock -= itemCart.cantidad
        await guardarCambioFisicoBD(itemInv)
      }
    }
    pedidoActual.value = []
    procesando.value = false
    alert('¡Material entregado y registrado en la base de datos central!')
  }, 2000)
}
</script>

<style scoped>
/* 🔐 ESTILOS DEL INTERCEPTOR DE CREDENCIALES */
.licencia-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(9, 9, 11, 0.96);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 9999;
  padding: 20px;
}
.licencia-modal-card {
  background: #18181b;
  border: 1px solid #27272a;
  border-radius: 20px;
  padding: 40px;
  max-width: 480px;
  width: 100%;
  text-align: center;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);
}
.modal-lock-icon {
  font-size: 40px;
  margin-bottom: 15px;
}
.licencia-modal-card h2 {
  font-size: 22px;
  font-weight: 900;
  margin-bottom: 10px;
  color: #ffffff;
}
.modal-instruction {
  font-size: 13px;
  color: #a1a1aa;
  line-height: 1.5;
  margin-bottom: 25px;
}
.modal-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  text-align: left;
}
.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.form-group label {
  font-size: 11px;
  font-weight: bold;
  text-transform: uppercase;
  color: #a1a1aa;
  letter-spacing: 0.5px;
}
.modal-input {
  background: #09090b;
  border: 1px solid #27272a;
  color: #ffffff;
  border-radius: 10px;
  padding: 12px 14px;
  font-size: 14px;
  outline: none;
  width: 100%;
}
.modal-input:focus {
  border-color: #b91c1c;
}
.error-validation-banner {
  background: rgba(185, 28, 28, 0.1);
  border: 1px solid rgba(185, 28, 28, 0.3);
  color: #ef4444;
  padding: 12px;
  border-radius: 10px;
  font-size: 12px;
  text-align: center;
}
.btn-submit-modal {
  background: #b91c1c;
  color: white;
  border: none;
  padding: 14px;
  border-radius: 10px;
  font-weight: 900;
  font-size: 13px;
  cursor: pointer;
  letter-spacing: 0.5px;
  margin-top: 10px;
  width: 100%;
}
.btn-submit-modal:hover:not(:disabled) {
  background: #991b1b;
}
.btn-submit-modal:disabled {
  background: #27272a;
  color: #a1a1aa;
  cursor: not-allowed;
}

/* 🚨 SUSPENSIÓN ELECTRÓNICA REMOTA */
.corte-pago-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: radial-gradient(circle, #1e0505 0%, #090000 100%);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 99999;
  padding: 20px;
}
.corte-card {
  background: #110101;
  border: 3px solid #7f1d1d;
  border-radius: 24px;
  padding: 50px;
  max-width: 550px;
  width: 100%;
  text-align: center;
  box-shadow: 0 0 50px rgba(220, 38, 38, 0.2);
}
.corte-icon {
  font-size: 64px;
  display: block;
  margin-bottom: 20px;
  animation: parpadeoAlerta 1.5s infinite;
}
.corte-card h2 {
  color: #ef4444;
  font-size: 32px;
  font-weight: 900;
  letter-spacing: 2px;
}
.corte-error-code {
  font-family: monospace;
  color: #7f1d1d;
  font-size: 12px;
  margin-bottom: 25px;
}
.corte-divider {
  height: 2px;
  background: linear-gradient(to right, transparent, #7f1d1d, transparent);
  margin-bottom: 25px;
}
.corte-desc {
  color: #fca5a5;
  font-size: 14px;
  line-height: 1.6;
  margin-bottom: 20px;
}
.corte-action {
  color: #a1a1aa;
  font-size: 13px;
}

@keyframes parpadeoAlerta {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.5;
    transform: scale(0.95);
  }
}

/* 🎨 REPARACIÓN 2: RESTAURADAS ANIMACIONES INTERACTIVAS (HOVER) */
.coil-slot {
  background: var(--bg-slot);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 20px 10px;
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
  cursor: pointer;
  transition:
    transform 0.2s cubic-bezier(0.4, 0, 0.2, 1),
    border-color 0.2s ease,
    box-shadow 0.2s ease;
}
.coil-slot:hover:not(.out-of-stock):not(.slot-admin-mode) {
  border-color: #b91c1c;
  transform: translateY(-4px) scale(1.02);
  box-shadow: 0 10px 20px rgba(185, 28, 28, 0.15);
}

.maquina-dashboard {
  min-height: 100vh;
}
.maquina-dark {
  background: #09090b;
  color: #f4f4f5;
  --bg-widget: #18181b;
  --bg-slot: #09090b;
  --border-color: #27272a;
  --text-title: #ffffff;
  --text-note: #a1a1aa;
  --bg-admin-btn: #27272a;
  --bg-input-adm: #18181b;
}
.dashboard-content {
  padding: 30px 5%;
  max-width: 1400px;
  margin: 0 auto;
}
.dash-header {
  margin-bottom: 30px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border-color);
}
.header-flex-container {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.dash-title {
  margin: 0;
  color: #b91c1c;
  font-weight: 900;
}
.dash-subtitle {
  color: var(--text-note);
  font-size: 14px;
  margin-top: 5px;
}
.status-online {
  color: #10b981;
  font-weight: bold;
}
.btn-admin-off {
  background: var(--bg-admin-btn);
  color: var(--text-title);
  border: 1px solid var(--border-color);
  padding: 8px 16px;
  border-radius: 10px;
  font-weight: bold;
  cursor: pointer;
}
.btn-admin-on {
  background: #d97706;
  color: white;
  border: 1px solid #d97706;
  padding: 8px 16px;
  border-radius: 10px;
  font-weight: bold;
  cursor: pointer;
}
.owner-alert-banner {
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.3);
  color: #d97706;
  padding: 12px;
  border-radius: 12px;
  font-size: 12px;
  margin-bottom: 25px;
  text-align: left;
}
.main-grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 25px;
}
.widget {
  background: var(--bg-widget);
  border: 1px solid var(--border-color);
  border-radius: 16px;
  padding: 25px;
  margin-bottom: 25px;
}
.widget h3 {
  margin: 0 0 15px 0;
  font-size: 14px;
  font-weight: 900;
  color: var(--text-note);
  text-transform: uppercase;
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 10px;
  text-align: left;
}
.coil-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 15px;
}
.slot-id {
  position: absolute;
  top: 6px;
  left: 8px;
  font-size: 10px;
  font-weight: bold;
  color: var(--text-note);
}
.slot-name {
  font-size: 13px;
  font-weight: bold;
  margin: 12px 0 6px 0;
}
.slot-name-admin {
  font-size: 12px;
  font-weight: bold;
  margin: 15px 0 10px 0;
  color: #d97706;
}
.slot-price {
  font-size: 15px;
  color: #b91c1c;
  font-weight: 900;
}
.slot-stock {
  font-size: 10px;
  color: var(--text-note);
  margin-top: 5px;
}
.admin-input-group {
  width: 100%;
  display: flex;
  flex-direction: column;
  text-align: left;
  margin-bottom: 8px;
}
.admin-input-group label {
  font-size: 9px;
  font-weight: bold;
  color: var(--text-note);
}
.input-admin-field {
  background: var(--bg-input-adm);
  border: 1px solid var(--border-color);
  color: var(--text-title);
  border-radius: 6px;
  padding: 4px 8px;
  font-size: 12px;
  width: 100%;
}
.admin-stock-adjuster {
  display: flex;
  align-items: center;
  background: var(--bg-input-adm);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  width: 100%;
}
.stock-qty-num {
  flex: 1;
  text-align: center;
  font-size: 11px;
  font-weight: bold;
}
.order-items {
  min-height: 100px;
}
.empty-order {
  text-align: center;
  color: var(--text-note);
  font-style: italic;
  padding-top: 30px;
}
.order-item {
  display: flex;
  justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid var(--border-color);
  font-size: 13px;
  text-align: left;
}
.quantity-controls {
  display: flex;
  align-items: center;
  background: var(--bg-slot);
  border: 1px solid var(--border-color);
  border-radius: 8px;
}
.btn-qty {
  background: transparent;
  border: none;
  width: 24px;
  height: 24px;
  color: var(--text-title);
  cursor: pointer;
}
.qty-number {
  padding: 0 6px;
  font-weight: bold;
}
.btn-confirm {
  width: 100%;
  background: #b91c1c;
  color: white;
  border: none;
  padding: 14px;
  border-radius: 12px;
  font-weight: 900;
  cursor: pointer;
  margin-top: 15px;
}
.btn-owner-action {
  width: 100%;
  color: white;
  border: none;
  padding: 12px;
  border-radius: 8px;
  font-weight: bold;
  cursor: pointer;
}
.btn-emerald {
  background: #047857;
}
.btn-amber {
  background: #d97706;
}
.cargando-pantalla {
  padding: 100px;
  text-align: center;
  font-size: 18px;
  color: #a1a1aa;
}
</style>
