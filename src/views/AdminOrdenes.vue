<template>
  <div class="admin-page">
    <NavBar />

    <main class="admin-container">
      <header class="admin-header">
        <h1>Panel de Administración - Gestión de Pedidos</h1>
        <p class="subtitle">
          Monitoreo, cancelación, verificación de comprobantes y avisos de reembolso.
        </p>
      </header>

      <!-- Controles: Buscador y Filtros -->
      <div class="controles">
        <div class="search-box">
          <span class="search-icon">🔍</span>
          <input
            type="text"
            v-model="busqueda"
            placeholder="Buscar por cliente, producto, correo, UID o ID de pedido..."
            class="input-busqueda"
          />
        </div>

        <select v-model="filtroEstado" class="select-filtro">
          <option value="Todos">Todos los estados</option>
          <option v-for="estado in todosLosEstados" :key="estado" :value="estado">
            {{ estado }}
          </option>
        </select>
      </div>

      <!-- Tabla de Pedidos -->
      <div class="tabla-wrapper">
        <table class="tabla-pedidos">
          <thead>
            <tr>
              <th>ID Orden</th>
              <th>Cliente</th>
              <th>Comprobante</th>
              <th>Productos / Cantidad</th>
              <th>Estado Actual</th>
              <th>Total</th>
              <th>Acción / Cambiar Estado</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="pedido in pedidosFiltrados" :key="pedido.id">
              <td>
                <span class="order-badge">#{{ pedido.id }}</span>
                <span class="order-date-mini">{{ pedido.fechaCreacion || pedido.fecha }}</span>
              </td>
              <td>
                <div class="client-info">
                  <strong class="client-name">{{
                    obtenerInfoCliente(pedido).nombreCompleto
                  }}</strong>
                  <span class="client-email">✉️ {{ obtenerInfoCliente(pedido).email }}</span>
                  <span class="client-uid">UID: {{ obtenerInfoCliente(pedido).uid }}</span>
                </div>
              </td>
              <td>
                <button
                  v-if="pedido.comprobante"
                  class="btn-comprobante"
                  @click="abrirComprobante(pedido.comprobante)"
                >
                  🖼️ Ver Comprobante
                </button>
                <span v-else class="no-items">Sin archivo</span>
              </td>
              <td>
                <div class="order-items-cell">
                  <div
                    v-for="(item, idx) in obtenerItemsVisibles(pedido)"
                    :key="idx"
                    class="item-badge"
                  >
                    <span class="item-qty">{{ getItemCant(item) }}x</span>
                    <span class="item-name">{{ getItemNombre(item) }}</span>
                  </div>

                  <button
                    v-if="obtenerItemsNormalizados(pedido).length > 2"
                    class="btn-expandir-items"
                    @click.stop="toggleExpandir(pedido.id)"
                  >
                    {{
                      estaExpandido(pedido.id)
                        ? '▲ Ver menos'
                        : `▼ +${obtenerItemsNormalizados(pedido).length - 2} más`
                    }}
                  </button>
                </div>
              </td>
              <td>
                <span :class="['status-chip', getEstadoClass(pedido.estado)]">
                  {{ pedido.estado }}
                </span>
              </td>
              <td class="total-cell">${{ Number(pedido.total || 0).toFixed(2) }} USD</td>
              <td>
                <div class="actions-cell">
                  <!-- SELECTOR CON ESTADOS -->
                  <select
                    :value="pedido.estado"
                    @change="
                      manejarCambioEstado(
                        pedido,
                        ($event.target as HTMLSelectElement).value as EstadoOrden,
                      )
                    "
                    class="select-estado"
                  >
                    <option v-for="estado in todosLosEstados" :key="estado" :value="estado">
                      {{ estado }}
                    </option>
                  </select>

                  <!-- BOTÓN DE CANCELACIÓN DIRECTA -->
                  <button
                    v-if="pedido.estado !== 'Cancelado'"
                    class="btn-cancelar-orden"
                    @click="cancelarOrdenDirecto(pedido)"
                    title="Cancelar pedido y notificar reembolso al cliente"
                  >
                    🚫 Cancelar
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="pedidosFiltrados.length === 0">
              <td colspan="7" class="empty-table">No se encontraron pedidos.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </main>

    <!-- MODAL COMPROBANTE -->
    <div v-if="modalComprobanteUrl" class="modal-overlay" @click="modalComprobanteUrl = ''">
      <div class="modal-img-card" @click.stop>
        <h3>Comprobante Adjunto</h3>
        <img :src="modalComprobanteUrl" alt="Comprobante de Pago" />
        <button class="btn-close-modal" @click="modalComprobanteUrl = ''">CERRAR</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  getDatabase,
  ref as dbRef,
  onValue,
  get,
  update,
  type Unsubscribe,
} from 'firebase/database'
import NavBar from '@/components/NavBar.vue'

export type EstadoOrden =
  | 'En proceso'
  | 'En preparacion'
  | 'Empacado'
  | 'En camino'
  | 'Entregado'
  | 'Cancelado'

export interface ItemOrden {
  id?: string
  nombre?: string
  cant?: number
  cantidad?: number
  precioUnitario?: number
  precio?: number
}

export interface UsuarioInfo {
  nombre?: string
  apellidos?: string
  email?: string
  uid?: string
}

export interface Orden {
  id: string
  userId: string
  nombreCliente?: string
  fecha?: string
  fechaCreacion?: string
  total?: number
  comprobante?: string
  estado: EstadoOrden
  items?: Record<string, ItemOrden> | ItemOrden[]
}

const pedidos = ref<Orden[]>([])
const usuariosMap = ref<Record<string, UsuarioInfo>>({})
const busqueda = ref<string>('')
const filtroEstado = ref<EstadoOrden | 'Todos'>('Todos')
const ordenesExpandidas = ref<Record<string, boolean>>({})
const modalComprobanteUrl = ref<string>('')

const todosLosEstados: EstadoOrden[] = [
  'En proceso',
  'En preparacion',
  'Empacado',
  'En camino',
  'Entregado',
  'Cancelado',
]

let unsubOrdenes: Unsubscribe | null = null
let unsubUsuarios: Unsubscribe | null = null

const SCRIPT_EMAIL_URL =
  'https://script.google.com/macros/s/AKfycbx8VdglYn-7wYotj6WCK3t3bP8Is6-X_B3RSBdlP1ZDj-VFNw27o_63WmQZccVsXizu/exec'

const toggleExpandir = (id: string) => {
  ordenesExpandidas.value[id] = !ordenesExpandidas.value[id]
}

const estaExpandido = (id: string): boolean => !!ordenesExpandidas.value[id]

const obtenerItemsVisibles = (pedido: Orden): ItemOrden[] => {
  const items = obtenerItemsNormalizados(pedido)
  if (estaExpandido(pedido.id) || items.length <= 2) return items
  return items.slice(0, 2)
}

const abrirComprobante = (url: string) => {
  modalComprobanteUrl.value = url
}

const cargarPerfilIndividual = async (uid: string) => {
  if (!uid || usuariosMap.value[uid]) return
  try {
    const db = getDatabase()
    const snap = await get(dbRef(db, `usuarios/${uid}`))
    if (snap.exists()) {
      const u = snap.val()
      usuariosMap.value = {
        ...usuariosMap.value,
        [uid]: {
          nombre: u.nombre || '',
          apellidos: u.apellidos || '',
          email: u.email || '',
          uid: u.uid || uid,
        },
      }
    }
  } catch (e) {
    console.error(`Error al consultar perfil para UID ${uid}:`, e)
  }
}

onMounted(() => {
  const db = getDatabase()

  const usuariosRef = dbRef(db, 'usuarios')
  unsubUsuarios = onValue(
    usuariosRef,
    (snapshot) => {
      const data = snapshot.val() || {}
      const map: Record<string, UsuarioInfo> = {}
      Object.keys(data).forEach((uidKey) => {
        const u = data[uidKey]
        map[uidKey] = {
          nombre: u.nombre || '',
          apellidos: u.apellidos || '',
          email: u.email || '',
          uid: u.uid || uidKey,
        }
      })
      usuariosMap.value = map
    },
    (error) => {
      console.warn('Lectura global de usuarios limitada:', error)
    },
  )

  const ordenesRef = dbRef(db, 'ordenes')
  unsubOrdenes = onValue(ordenesRef, (snapshot) => {
    const data = snapshot.val()
    if (!data) {
      pedidos.value = []
      return
    }
    const listaFormateada: Orden[] = []
    Object.keys(data).forEach((userId) => {
      cargarPerfilIndividual(userId)

      const ordenesUsuario = data[userId]
      if (ordenesUsuario && typeof ordenesUsuario === 'object') {
        Object.keys(ordenesUsuario).forEach((ordenId) => {
          listaFormateada.push({
            userId,
            id: ordenId,
            ...ordenesUsuario[ordenId],
          })
        })
      }
    })
    pedidos.value = listaFormateada.reverse()
  })
})

onUnmounted(() => {
  if (unsubOrdenes) unsubOrdenes()
  if (unsubUsuarios) unsubUsuarios()
})

const obtenerItemsNormalizados = (pedido: Orden): ItemOrden[] => {
  if (!pedido.items) return []
  return Array.isArray(pedido.items) ? pedido.items : Object.values(pedido.items)
}

const getItemCant = (item: ItemOrden): number => item.cant || item.cantidad || 1
const getItemNombre = (item: ItemOrden): string => item.nombre || item.id || 'Producto'

const obtenerInfoCliente = (pedido: Orden) => {
  const u = usuariosMap.value[pedido.userId]
  if (u && (u.nombre || u.apellidos || u.email)) {
    const nombreCompleto = `${u.nombre || ''} ${u.apellidos || ''}`.trim()
    return {
      nombreCompleto: nombreCompleto || 'Usuario Registrado',
      email: u.email || '',
      uid: u.uid || pedido.userId,
    }
  }
  return {
    nombreCompleto: pedido.nombreCliente || `Cliente #${pedido.userId.slice(0, 6)}`,
    email: '',
    uid: pedido.userId || 'N/A',
  }
}

const cancelarOrdenDirecto = (pedido: Orden) => {
  const deAcuerdo = confirm(
    `¿Estás seguro de cancelar la orden #${pedido.id}? Se enviará una notificación por correo indicando la devolución del dinero.`,
  )
  if (deAcuerdo) {
    manejarCambioEstado(pedido, 'Cancelado')
  }
}

const manejarCambioEstado = async (pedido: Orden, nuevoEstado: EstadoOrden) => {
  if (nuevoEstado === 'Cancelado' && pedido.estado !== 'Cancelado') {
    const confirmar = confirm(
      `¿Deseas cambiar el estado de la orden #${pedido.id} a CANCELADO? Se notificará al cliente por correo.`,
    )
    if (!confirmar) return
  }

  const db = getDatabase()
  await update(dbRef(db, `ordenes/${pedido.userId}/${pedido.id}`), { estado: nuevoEstado })

  const infoCliente = obtenerInfoCliente(pedido)
  if (infoCliente.email) {
    enviarCorreoNotificacion(
      infoCliente.email,
      infoCliente.nombreCompleto,
      pedido.id,
      nuevoEstado,
      pedido,
    )
  }
}

const enviarCorreoNotificacion = async (
  emailDestino: string,
  nombreCliente: string,
  idOrden: string,
  nuevoEstado: string,
  pedido: Orden,
) => {
  const itemsHtml = obtenerItemsNormalizados(pedido)
    .map((i) => `<li><b>${getItemCant(i)}x</b> ${getItemNombre(i)}</li>`)
    .join('')

  try {
    await fetch(SCRIPT_EMAIL_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain;charset=utf-8' },
      body: JSON.stringify({
        accion: 'notificarEstado',
        email: emailDestino,
        nombre: nombreCliente,
        ordenId: idOrden,
        estado: nuevoEstado,
        total: Number(pedido.total || 0).toFixed(2),
        itemsHtml: itemsHtml,
      }),
    })
  } catch (err) {
    console.error('Error notificando por correo:', err)
  }
}

const getEstadoClass = (estado: string) => {
  const st = (estado || '').toLowerCase()
  if (st.includes('cancela')) return 'status-canc'
  if (st.includes('proceso')) return 'status-proc'
  if (st.includes('preparacion')) return 'status-prep'
  if (st.includes('empacado')) return 'status-pack'
  if (st.includes('camino')) return 'status-way'
  if (st.includes('entregado')) return 'status-done'
  return ''
}

const pedidosFiltrados = computed<Orden[]>(() => {
  const termino = busqueda.value.toLowerCase().trim()
  return pedidos.value.filter((pedido) => {
    const coincideEstado = filtroEstado.value === 'Todos' || pedido.estado === filtroEstado.value
    const info = obtenerInfoCliente(pedido)
    const nombre = info.nombreCompleto.toLowerCase()
    const email = info.email.toLowerCase()
    const idOrden = pedido.id.toLowerCase()

    return (
      coincideEstado &&
      (!termino || nombre.includes(termino) || email.includes(termino) || idOrden.includes(termino))
    )
  })
})
</script>

<style scoped>
.admin-page {
  min-height: 100vh;
  background: var(--bg-main, #f8fafc);
  color: var(--text-main, #0f172a);
}

.admin-container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 30px 20px;
}

.admin-header h1 {
  font-size: 1.8rem;
  font-weight: 800;
  margin: 0 0 6px 0;
}

.subtitle {
  color: var(--text-muted, #64748b);
  margin: 0 0 25px 0;
}

.controles {
  display: flex;
  gap: 15px;
  margin-bottom: 25px;
  flex-wrap: wrap;
}

.search-box {
  position: relative;
  flex: 1;
  min-width: 280px;
}

.search-icon {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  opacity: 0.6;
}

.input-busqueda {
  width: 100%;
  padding: 10px 12px 10px 36px;
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #cbd5e1);
  border-radius: 8px;
  color: var(--text-main, #0f172a);
  font-size: 0.9rem;
  box-sizing: border-box;
}

.select-filtro,
.select-estado {
  padding: 10px 14px;
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #cbd5e1);
  border-radius: 8px;
  color: var(--text-main, #0f172a);
  font-weight: 600;
  cursor: pointer;
}

.actions-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.btn-cancelar-orden {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 0.8rem;
  font-weight: 800;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn-cancelar-orden:hover {
  background: #dc2626;
  color: #ffffff;
  border-color: #dc2626;
}

.tabla-wrapper {
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
}

.tabla-pedidos {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}

.tabla-pedidos th {
  background-color: var(--bg-input, #f1f5f9);
  color: var(--text-main, #0f172a);
  padding: 14px 16px;
  font-size: 0.85rem;
  font-weight: 800;
  text-transform: uppercase;
  border-bottom: 1px solid var(--border, #e2e8f0);
}

.tabla-pedidos td {
  padding: 16px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  color: var(--text-main, #0f172a);
  font-size: 0.9rem;
  vertical-align: middle;
}

.order-badge {
  font-weight: 800;
  color: var(--accent, #e52e2e);
  display: block;
}

.order-date-mini {
  font-size: 0.75rem;
  color: var(--text-muted, #64748b);
}

.client-info {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.client-name {
  font-size: 0.95rem;
  font-weight: 800;
  color: var(--text-main, #0f172a);
}

.client-email {
  font-size: 0.82rem;
  color: var(--text-muted, #64748b);
}

.client-uid {
  font-size: 0.72rem;
  font-family: monospace;
  background: var(--bg-input, #f1f5f9);
  color: var(--text-muted, #475569);
  padding: 2px 6px;
  border-radius: 4px;
  width: fit-content;
  border: 1px solid var(--border, #e2e8f0);
  margin-top: 2px;
}

.btn-comprobante {
  background: #e0f2fe;
  color: #0369a1;
  border: 1px solid #bae6fd;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 0.8rem;
  font-weight: 800;
  cursor: pointer;
}

.order-items-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 240px;
}

.item-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--bg-input, #f1f5f9);
  padding: 3px 6px;
  border-radius: 4px;
  font-size: 0.8rem;
  border: 1px solid var(--border, #e2e8f0);
}

.item-qty {
  font-weight: 800;
  color: #dc2626;
  background: rgba(220, 38, 38, 0.12);
  padding: 1px 5px;
  border-radius: 3px;
}

.item-name {
  font-weight: 700;
  color: var(--text-main, #0f172a);
}

.btn-expandir-items {
  background: transparent;
  border: none;
  color: var(--accent, #e52e2e);
  font-size: 0.75rem;
  font-weight: 800;
  cursor: pointer;
  padding: 2px 0;
  text-align: left;
}

.total-cell {
  font-weight: 800;
}

.status-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 6px 12px;
  border-radius: 20px;
  font-size: 0.75rem;
  font-weight: 800;
  text-transform: uppercase;
  white-space: nowrap;
}

.status-proc {
  background: #f3e8ff;
  color: #6b21a8;
  border: 1px solid #e9d5ff;
}
.status-prep {
  background: #fef3c7;
  color: #92400e;
  border: 1px solid #fde68a;
}
.status-pack {
  background: #e0e7ff;
  color: #3730a3;
  border: 1px solid #c7d2fe;
}
.status-way {
  background: #e0f2fe;
  color: #075985;
  border: 1px solid #bae6fd;
}
.status-done {
  background: #dcfce7;
  color: #166534;
  border: 1px solid #bbf7d0;
}
.status-canc {
  background: #fee2e2;
  color: #991b1b;
  border: 1px solid #fca5a5;
}

.empty-table {
  text-align: center;
  padding: 40px !important;
  color: var(--text-muted, #64748b);
  font-style: italic;
}

.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.8);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.modal-img-card {
  background: var(--bg-panel, #ffffff);
  padding: 20px;
  border-radius: 12px;
  max-width: 500px;
  text-align: center;
}

.modal-img-card img {
  max-width: 100%;
  max-height: 70vh;
  object-fit: contain;
  border-radius: 8px;
  margin: 15px 0;
}

.btn-close-modal {
  background: #e52e2e;
  color: white;
  border: none;
  padding: 10px 20px;
  border-radius: 6px;
  font-weight: bold;
  cursor: pointer;
}
</style>
