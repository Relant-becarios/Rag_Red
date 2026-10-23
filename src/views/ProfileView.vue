<template>
  <div class="profile-page">
    <NavBar />

    <main class="profile-container">
      <header class="profile-header">
        <h1>Mi Perfil</h1>
        <p class="subtitle">
          Gestiona tus datos de cuenta, rastrea tus pedidos y consulta tus favoritos e historial.
        </p>
      </header>

      <!-- INFORMACIÓN DEL CLIENTE Y BADGE DE ADMIN -->
      <section class="user-card">
        <div class="user-avatar">
          <img
            v-if="perfilUsuario.foto"
            :src="perfilUsuario.foto"
            alt="Foto de perfil"
            class="avatar-img"
          />
          <span v-else>🔩</span>
        </div>

        <div class="user-details">
          <div class="user-title-row">
            <h2>{{ perfilUsuario.nombre || 'Usuario' }} {{ perfilUsuario.apellidos || '' }}</h2>
            <span v-if="perfilUsuario.rol === 'admin'" class="badge-admin">🛠️ Administrador</span>
          </div>

          <p><strong>Correo:</strong> {{ authStore.usuarioActual?.email }}</p>
          <p v-if="perfilUsuario.empresa"><strong>Empresa:</strong> {{ perfilUsuario.empresa }}</p>
          <p v-if="perfilUsuario.descripcion" class="user-bio">
            <strong>Descripción:</strong> {{ perfilUsuario.descripcion }}
          </p>
        </div>

        <button class="btn-edit-profile" @click="abrirModalEditar">⚙️ Editar Perfil</button>
      </section>

      <!-- NAVEGACIÓN POR PESTAÑAS (PEDIDOS / FAVORITOS / HISTORIAL) -->
      <nav class="profile-tabs">
        <button
          :class="['tab-btn', { active: tabActiva === 'pedidos' }]"
          @click="tabActiva = 'pedidos'"
        >
          📦 Mis Pedidos ({{ ordenes.length }})
        </button>
        <button
          :class="['tab-btn', { active: tabActiva === 'favoritos' }]"
          @click="tabActiva = 'favoritos'"
        >
          ❤️ Favoritos ({{ favoritosProductos.length }})
        </button>
        <button
          :class="['tab-btn', { active: tabActiva === 'historial' }]"
          @click="tabActiva = 'historial'"
        >
          🕒 Historial ({{ historialProductos.length }})
        </button>
      </nav>

      <!-- SECCIÓN 1: MIS PEDIDOS -->
      <section v-if="tabActiva === 'pedidos'" class="orders-section">
        <div v-if="cargando" class="loading-state">Cargando tus pedidos...</div>

        <div v-else-if="ordenes.length === 0" class="empty-orders">
          <p>Aún no has realizado ninguna compra.</p>
          <button class="btn-catalog" @click="$router.push('/catalogo')">Ir al Catálogo</button>
        </div>

        <div v-else class="orders-list">
          <article v-for="orden in ordenes" :key="orden.id" class="order-card">
            <header class="order-card-header">
              <div>
                <span class="order-id">Orden #{{ orden.id }}</span>
                <span class="order-date">{{ orden.fecha }}</span>
              </div>

              <div class="header-right">
                <div class="order-total">${{ Number(orden.total || 0).toFixed(2) }} USD</div>

                <button
                  v-if="puedeCancelarCliente(orden.estado)"
                  class="btn-cancelar-cliente"
                  @click="cancelarOrdenCliente(orden)"
                  title="Cancelar este pedido"
                >
                  🚫 Cancelar Pedido
                </button>
              </div>
            </header>

            <!-- BANNER SI LA ORDEN ESTÁ CANCELADA -->
            <div v-if="orden.estado === 'Cancelado'" class="cancelled-banner">
              ❌ Esta orden ha sido cancelada. Se ha notificado la devolución del pago a tu correo.
            </div>

            <!-- STEPPER DE SEGUIMIENTO -->
            <div v-else class="stepper-container">
              <!-- 1. EN PROCESO -->
              <div :class="['step-item', { active: obtenerNivelStep(orden.estado) >= 1 }]">
                <div class="step-circle">
                  <svg
                    viewBox="0 0 24 24"
                    width="22"
                    height="22"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                  >
                    <circle cx="12" cy="12" r="10"></circle>
                    <polyline points="12 6 12 12 16 14"></polyline>
                  </svg>
                </div>
                <span class="step-title">En proceso</span>
              </div>

              <div
                :class="['step-line', obtenerNivelStep(orden.estado) >= 2 ? 'solid' : 'dotted']"
              ></div>

              <!-- 2. EN PREPARACIÓN -->
              <div :class="['step-item', { active: obtenerNivelStep(orden.estado) >= 2 }]">
                <div class="step-circle">
                  <svg
                    viewBox="0 0 24 24"
                    width="22"
                    height="22"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                  >
                    <path
                      d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"
                    ></path>
                    <rect x="8" y="2" width="8" height="4" rx="1" ry="1"></rect>
                  </svg>
                </div>
                <span class="step-title">En preparación</span>
              </div>

              <div
                :class="['step-line', obtenerNivelStep(orden.estado) >= 3 ? 'solid' : 'dotted']"
              ></div>

              <!-- 3. EMPACADO -->
              <div :class="['step-item', { active: obtenerNivelStep(orden.estado) >= 3 }]">
                <div class="step-circle">
                  <svg
                    viewBox="0 0 24 24"
                    width="22"
                    height="22"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                  >
                    <path
                      d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"
                    ></path>
                  </svg>
                </div>
                <span class="step-title">Empacado</span>
              </div>

              <div
                :class="['step-line', obtenerNivelStep(orden.estado) >= 4 ? 'solid' : 'dotted']"
              ></div>

              <!-- 4. EN CAMINO -->
              <div :class="['step-item', { active: obtenerNivelStep(orden.estado) >= 4 }]">
                <div class="step-circle">
                  <svg
                    viewBox="0 0 24 24"
                    width="22"
                    height="22"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                  >
                    <rect x="1" y="3" width="15" height="13"></rect>
                    <polygon points="16 8 20 8 23 11 23 16 16 16 16 8"></polygon>
                    <circle cx="5.5" cy="18.5" r="2.5"></circle>
                    <circle cx="18.5" cy="18.5" r="2.5"></circle>
                  </svg>
                </div>
                <span class="step-title">En camino</span>
              </div>

              <div
                :class="['step-line', obtenerNivelStep(orden.estado) >= 5 ? 'solid' : 'dotted']"
              ></div>

              <!-- 5. ENTREGADO -->
              <div :class="['step-item', { active: obtenerNivelStep(orden.estado) >= 5 }]">
                <div class="step-circle">
                  <svg
                    viewBox="0 0 24 24"
                    width="22"
                    height="22"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                  >
                    <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
                    <polyline points="9 22 9 12 15 12 15 22"></polyline>
                  </svg>
                </div>
                <span class="step-title">Entregado</span>
              </div>
            </div>

            <!-- RESUMEN DE ARTÍCULOS -->
            <div class="order-items-summary">
              <div
                v-for="(item, idx) in obtenerItemsNormalizados(orden)"
                :key="idx"
                class="order-item-pill"
              >
                <strong>{{ item.cant || item.cantidad || 1 }}x</strong> {{ item.nombre || item.id }}
              </div>
            </div>
          </article>
        </div>
      </section>

      <!-- SECCIÓN 2: FAVORITOS -->
      <section v-else-if="tabActiva === 'favoritos'" class="tab-section">
        <div v-if="favoritosProductos.length === 0" class="empty-orders">
          <p>Aún no has guardado productos en tus favoritos.</p>
          <button class="btn-catalog" @click="$router.push('/catalogo')">Explorar Catálogo</button>
        </div>

        <div v-else class="products-grid">
          <ProductCard
            v-for="prod in favoritosProductos"
            :key="obtenerSkuProducto(prod)"
            :producto="prod"
          />
        </div>
      </section>

      <!-- SECCIÓN 3: HISTORIAL DE NAVEGACIÓN -->
      <section v-else-if="tabActiva === 'historial'" class="tab-section">
        <div v-if="historialProductos.length === 0" class="empty-orders">
          <p>No tienes productos registrados en tu historial reciente.</p>
          <button class="btn-catalog" @click="$router.push('/catalogo')">Ir al Catálogo</button>
        </div>

        <div v-else class="products-grid">
          <ProductCard
            v-for="prod in historialProductos"
            :key="obtenerSkuProducto(prod)"
            :producto="prod"
          />
        </div>
      </section>
    </main>

    <!-- MODAL 1: EDITAR PERFIL -->
    <div v-if="modalEditarAbierto" class="modal-overlay" @click="modalEditarAbierto = false">
      <div class="modal-card" @click.stop>
        <h3>⚙️ Configuración de Perfil</h3>

        <div class="form-group">
          <label class="form-label">Foto de Perfil:</label>
          <div class="photo-upload-box">
            <div class="preview-avatar">
              <img v-if="editForm.foto" :src="editForm.foto" alt="Vista previa" />
              <span v-else>🔩</span>
            </div>
            <div class="upload-controls">
              <input type="file" accept="image/*" @change="handleFotoUpload" class="file-input" />
              <button v-if="editForm.foto" class="btn-remove-photo" @click="editForm.foto = ''">
                ✕ Eliminar Foto
              </button>
            </div>
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Nombre:</label>
            <input
              type="text"
              v-model="editForm.nombre"
              placeholder="Tu nombre"
              class="input-text"
            />
          </div>
          <div class="form-group">
            <label class="form-label">Apellidos:</label>
            <input
              type="text"
              v-model="editForm.apellidos"
              placeholder="Tus apellidos"
              class="input-text"
            />
          </div>
        </div>

        <div class="form-group">
          <label class="form-label">Empresa:</label>
          <input
            type="text"
            v-model="editForm.empresa"
            placeholder="Nombre de tu empresa"
            class="input-text"
          />
        </div>

        <div class="form-group">
          <label class="form-label">Descripción / Biografía:</label>
          <textarea
            v-model="editForm.descripcion"
            rows="3"
            placeholder="Añade un cargo, departamento o breve descripción..."
            class="input-textarea"
          ></textarea>
        </div>

        <div class="modal-actions">
          <button
            class="btn-modal-cancel"
            @click="modalEditarAbierto = false"
            :disabled="guardandoPerfil"
          >
            Cancelar
          </button>
          <button class="btn-modal-save" @click="guardarPerfil" :disabled="guardandoPerfil">
            {{ guardandoPerfil ? 'Guardando...' : 'Guardar Cambios' }}
          </button>
        </div>
      </div>
    </div>

    <!-- MODAL 2: FICHA TÉCNICA Y DETALLE DE PRODUCTO -->
    <ProductModal />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { fetchProductos, type Producto } from '@/api/inventory'
import {
  getDatabase,
  ref as dbRef,
  onValue,
  update,
  runTransaction,
  type Unsubscribe,
} from 'firebase/database'
import { getAuth } from 'firebase/auth'
import NavBar from '@/components/NavBar.vue'
import ProductCard from '@/components/ProductCard.vue'
import ProductModal from '@/components/ProductModal.vue'

interface ItemOrden {
  id?: string
  nombre?: string
  cant?: number
  cantidad?: number
  precio?: number
}

interface OrdenCliente {
  id: string
  fecha?: string
  total?: number
  estado: string
  items?: Record<string, ItemOrden> | ItemOrden[]
}

type ProductoExtendido = Producto & {
  ID?: string | number
  SKU?: string | number
  'no. De parte'?: string | number
  NO_DE_PARTE?: string | number
  Producto?: string
  Precio?: string | number
  Imagen_Front_URL?: string
  Imagen_URL?: string
  Descripcion?: string
  descripcion?: string
  Categoria?: string
  categoria?: string
  Stock?: string | number
}

interface PerfilUsuario {
  uid?: string
  email?: string
  nombre?: string
  apellidos?: string
  rol?: string
  empresa?: string
  descripcion?: string
  foto?: string
}

const authStore = useAuthStore()

const tabActiva = ref<'pedidos' | 'favoritos' | 'historial'>('pedidos')

const ordenes = ref<OrdenCliente[]>([])
const favoritosRaw = ref<Record<string, unknown>[]>([])
const historialRaw = ref<Record<string, unknown>[]>([])
const productosCatalogo = ref<Producto[]>([])
const inventarioFirebase = ref<Record<string, number>>({})
const cargando = ref(true)

let unsubOrdenes: Unsubscribe | null = null
let unsubFavoritos: Unsubscribe | null = null
let unsubHistorial: Unsubscribe | null = null
let unsubInventario: Unsubscribe | null = null

const modalEditarAbierto = ref(false)
const guardandoPerfil = ref(false)

const editForm = ref({
  nombre: '',
  apellidos: '',
  empresa: '',
  descripcion: '',
  foto: '',
})

const perfilUsuario = computed<PerfilUsuario>(() => (authStore.perfil || {}) as PerfilUsuario)

const SCRIPT_URL =
  'https://script.google.com/macros/s/AKfycbx8VdglYn-7wYotj6WCK3t3bP8Is6-X_B3RSBdlP1ZDj-VFNw27o_63WmQZccVsXizu/exec'

onMounted(async () => {
  try {
    productosCatalogo.value = await fetchProductos()
  } catch (e) {
    console.error('Error cargando catálogo en perfil:', e)
  }

  const auth = getAuth()
  const user = auth.currentUser || authStore.usuarioActual
  const db = getDatabase()

  // 1. Escuchar Inventario en tiempo real desde Firebase
  const invRef = dbRef(db, 'inventario')
  unsubInventario = onValue(invRef, (snap) => {
    inventarioFirebase.value = snap.val() || {}
  })

  if (user && user.uid) {
    // 2. Mis Pedidos
    const misOrdenesRef = dbRef(db, `ordenes/${user.uid}`)
    unsubOrdenes = onValue(misOrdenesRef, (snapshot) => {
      const data = snapshot.val()
      if (data) {
        const lista: OrdenCliente[] = Object.keys(data).map((key) => ({
          id: key,
          ...data[key],
        }))
        ordenes.value = lista.reverse()
      } else {
        ordenes.value = []
      }
      cargando.value = false
    })

    // 3. Mis Favoritos
    const favsRef = dbRef(db, `favoritos/${user.uid}`)
    unsubFavoritos = onValue(favsRef, (snapshot) => {
      const data = snapshot.val()
      if (data) {
        favoritosRaw.value = Object.keys(data).map((key) => {
          const item = data[key]
          return typeof item === 'object' && item !== null ? { id: key, ...item } : { id: key }
        })
      } else {
        favoritosRaw.value = []
      }
    })

    // 4. Historial
    const histRef = dbRef(db, `historial/${user.uid}`)
    unsubHistorial = onValue(histRef, (snapshot) => {
      const data = snapshot.val()
      if (data) {
        historialRaw.value = Object.keys(data)
          .map((key) => {
            const item = data[key]
            return typeof item === 'object' && item !== null ? { id: key, ...item } : { id: key }
          })
          .reverse()
      } else {
        historialRaw.value = []
      }
    })
  } else {
    cargando.value = false
  }
})

onUnmounted(() => {
  if (unsubOrdenes) unsubOrdenes()
  if (unsubFavoritos) unsubFavoritos()
  if (unsubHistorial) unsubHistorial()
  if (unsubInventario) unsubInventario()
})

const obtenerSkuProducto = (prod: Producto): string => {
  const pExt = prod as ProductoExtendido
  return String(
    pExt.id || pExt.ID || pExt.SKU || pExt['no. De parte'] || pExt.NO_DE_PARTE || '',
  ).trim()
}

// Cruzar productos con existencias en vivo de Firebase Realtime Database
const enriquecerProducto = (rawItem: Record<string, unknown>): Producto => {
  const rawId = String(rawItem.id || rawItem.ID || '')
    .trim()
    .toLowerCase()

  const encontrado = productosCatalogo.value.find((p) => {
    const sku = obtenerSkuProducto(p).toLowerCase()
    return sku === rawId
  })

  const prodBase = (
    encontrado ? { ...encontrado } : { ...rawItem, id: rawItem.id }
  ) as ProductoExtendido

  // Reemplazar stock con la validación en tiempo real de Firebase
  const skuKey = rawId
  if (typeof inventarioFirebase.value[skuKey] === 'number') {
    prodBase.Stock = inventarioFirebase.value[skuKey]
  }

  return prodBase as Producto
}

const favoritosProductos = computed<Producto[]>(() =>
  favoritosRaw.value.map((item) => enriquecerProducto(item)),
)

const historialProductos = computed<Producto[]>(() =>
  historialRaw.value.map((item) => enriquecerProducto(item)),
)

const abrirModalEditar = () => {
  editForm.value = {
    nombre: perfilUsuario.value.nombre || '',
    apellidos: perfilUsuario.value.apellidos || '',
    empresa: perfilUsuario.value.empresa || '',
    descripcion: perfilUsuario.value.descripcion || '',
    foto: perfilUsuario.value.foto || '',
  }
  modalEditarAbierto.value = true
}

const handleFotoUpload = (e: Event) => {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (file) {
    const reader = new FileReader()
    reader.onload = (evt) => {
      editForm.value.foto = evt.target?.result as string
    }
    reader.readAsDataURL(file)
  }
}

const guardarPerfil = async () => {
  const auth = getAuth()
  const user = auth.currentUser || authStore.usuarioActual
  if (!user || !user.uid) return

  guardandoPerfil.value = true
  try {
    const db = getDatabase()
    const updates = {
      nombre: editForm.value.nombre,
      apellidos: editForm.value.apellidos,
      empresa: editForm.value.empresa,
      descripcion: editForm.value.descripcion,
      foto: editForm.value.foto,
    }

    await update(dbRef(db, `usuarios/${user.uid}`), updates)

    if (authStore.perfil) {
      Object.assign(authStore.perfil, updates)
    }

    modalEditarAbierto.value = false
  } catch (e) {
    console.error('Error guardando perfil:', e)
    alert('Ocurrió un error al actualizar los datos de tu perfil.')
  } finally {
    guardandoPerfil.value = false
  }
}

const puedeCancelarCliente = (estado: string): boolean => {
  const st = (estado || '').toLowerCase()
  return st.includes('proceso') || st.includes('preparacion') || st.includes('preparación')
}

const cancelarOrdenCliente = async (orden: OrdenCliente) => {
  const deAcuerdo = confirm(
    `¿Estás seguro de que deseas cancelar la orden #${orden.id}? Se procesará la cancelación y te enviaremos la confirmación de reembolso a tu correo.`,
  )
  if (!deAcuerdo) return

  const auth = getAuth()
  const user = auth.currentUser || authStore.usuarioActual
  if (!user || !user.uid) return

  const itemsNormalizados = obtenerItemsNormalizados(orden)

  try {
    const db = getDatabase()

    // 1. Restaurar stock en Firebase
    const inventarioRef = dbRef(db, 'inventario')
    await runTransaction(inventarioRef, (inventarioActual) => {
      const nuevoInventario = inventarioActual ? { ...inventarioActual } : {}
      for (const item of itemsNormalizados) {
        const key = String(item.id || '')
          .trim()
          .toLowerCase()
        const cantRestaurar = Number(item.cant || item.cantidad || 1)
        const stockActual = typeof nuevoInventario[key] === 'number' ? nuevoInventario[key] : 0
        nuevoInventario[key] = stockActual + cantRestaurar
      }
      return nuevoInventario
    })

    // 2. Cambiar estado a Cancelado
    await update(dbRef(db, `ordenes/${user.uid}/${orden.id}`), { estado: 'Cancelado' })

    const nombreCliente =
      `${perfilUsuario.value.nombre || ''} ${perfilUsuario.value.apellidos || ''}`.trim() ||
      user.email ||
      'Cliente'
    const emailCliente = user.email || perfilUsuario.value.email || ''

    if (emailCliente) {
      const itemsHtml = itemsNormalizados
        .map((i) => `<li><b>${i.cant || i.cantidad || 1}x</b> ${i.nombre || i.id}</li>`)
        .join('')

      await fetch(SCRIPT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: JSON.stringify({
          accion: 'notificarEstado',
          email: emailCliente,
          nombre: nombreCliente,
          ordenId: orden.id,
          estado: 'Cancelado',
          total: Number(orden.total || 0).toFixed(2),
          itemsHtml: itemsHtml,
        }),
      })

      // 3. Restaurar stock en Sheets
      await fetch(SCRIPT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: JSON.stringify({
          accion: 'restaurarStock',
          items: itemsNormalizados.map((i) => ({ id: i.id, cantidad: i.cant || i.cantidad || 1 })),
        }),
      })
    }
  } catch (e) {
    console.error('Error al cancelar la orden:', e)
    alert('Ocurrió un error al intentar cancelar tu pedido. Intenta de nuevo.')
  }
}

const obtenerItemsNormalizados = (orden: OrdenCliente): ItemOrden[] => {
  if (!orden.items) return []
  return Array.isArray(orden.items) ? orden.items : Object.values(orden.items)
}

const obtenerNivelStep = (estado: string): number => {
  const st = (estado || '').toLowerCase()
  if (st.includes('proceso')) return 1
  if (st.includes('preparacion') || st.includes('preparación')) return 2
  if (st.includes('empacado')) return 3
  if (st.includes('camino')) return 4
  if (st.includes('entregado')) return 5
  return 0
}
</script>

<style scoped>
.profile-page {
  min-height: 100vh;
  background-color: var(--bg-main, #f8fafc);
  color: var(--text-main, #0f172a);
}

.profile-container {
  max-width: 1000px;
  margin: 0 auto;
  padding: 30px 20px 80px 20px;
}

.profile-header h1 {
  font-size: 1.8rem;
  font-weight: 800;
  margin: 0 0 6px 0;
}

.subtitle {
  color: var(--text-muted, #64748b);
  margin-bottom: 30px;
}

.user-card {
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 12px;
  padding: 24px;
  display: flex;
  align-items: center;
  gap: 20px;
  margin-bottom: 25px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
  flex-wrap: wrap;
}

.user-avatar {
  font-size: 2.2rem;
  background: var(--bg-input, #f1f5f9);
  width: 75px;
  height: 75px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border: 2px solid var(--border, #cbd5e1);
  flex-shrink: 0;
}

.avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.user-details {
  flex-grow: 1;
}

.user-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}

.user-details h2 {
  font-size: 1.25rem;
  margin: 0;
  font-weight: 800;
}

.badge-admin {
  background: #fef3c7;
  color: #92400e;
  border: 1px solid #fde68a;
  font-size: 0.75rem;
  font-weight: 800;
  padding: 3px 10px;
  border-radius: 20px;
}

.user-details p {
  margin: 3px 0;
  font-size: 0.9rem;
  color: var(--text-muted, #64748b);
}

.user-bio {
  margin-top: 6px !important;
  color: var(--text-main, #334155) !important;
  font-style: italic;
}

.btn-edit-profile {
  background: var(--bg-input, #f1f5f9);
  border: 1px solid var(--border, #cbd5e1);
  color: var(--text-main, #0f172a);
  padding: 9px 16px;
  border-radius: 8px;
  font-weight: 700;
  font-size: 0.88rem;
  cursor: pointer;
  transition: all 0.2s ease;
  margin-left: auto;
}

.btn-edit-profile:hover {
  background: var(--accent, #e52e2e);
  color: #ffffff;
  border-color: var(--accent, #e52e2e);
}

/* PESTAÑAS (TABS) */
.profile-tabs {
  display: flex;
  gap: 10px;
  margin-bottom: 25px;
  border-bottom: 2px solid var(--border, #e2e8f0);
  padding-bottom: 2px;
}

.tab-btn {
  background: none;
  border: none;
  padding: 10px 18px;
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--text-muted, #64748b);
  cursor: pointer;
  border-bottom: 3px solid transparent;
  transition: all 0.2s ease;
}

.tab-btn.active {
  color: var(--accent, #e52e2e);
  border-bottom-color: var(--accent, #e52e2e);
}

.tab-section {
  min-height: 200px;
}

.loading-state,
.empty-orders {
  text-align: center;
  padding: 50px 0;
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 12px;
  color: var(--text-muted, #64748b);
}

.btn-catalog {
  margin-top: 15px;
  background: var(--accent, #e52e2e);
  color: white;
  border: none;
  padding: 10px 20px;
  border-radius: 8px;
  font-weight: bold;
  cursor: pointer;
}

.orders-list {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.order-card {
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
}

.order-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border, #e2e8f0);
  padding-bottom: 15px;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 10px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 15px;
}

.order-id {
  font-size: 1.05rem;
  font-weight: 800;
  color: var(--text-main, #0f172a);
  margin-right: 12px;
}

.order-date {
  font-size: 0.85rem;
  color: var(--text-muted, #64748b);
}

.order-total {
  font-size: 1.2rem;
  font-weight: 900;
  color: var(--accent, #e52e2e);
}

.btn-cancelar-cliente {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 0.8rem;
  font-weight: 800;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn-cancelar-cliente:hover {
  background: #dc2626;
  color: #ffffff;
  border-color: #dc2626;
}

.cancelled-banner {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
  padding: 14px 18px;
  border-radius: 8px;
  font-weight: 700;
  font-size: 0.9rem;
  margin: 15px 0 20px 0;
  text-align: center;
}

.stepper-container {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 25px 0;
  padding: 0 10px;
}

.step-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  color: var(--text-muted, #94a3b8);
  transition: color 0.3s ease;
}

.step-item.active {
  color: var(--accent, #e52e2e);
}

.step-circle {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  background: var(--bg-input, #f1f5f9);
  border: 2px solid var(--border, #cbd5e1);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.3s ease;
}

.step-item.active .step-circle {
  background: var(--accent, #e52e2e);
  border-color: var(--accent, #e52e2e);
  color: #ffffff;
}

.step-title {
  font-size: 0.75rem;
  font-weight: 700;
  text-align: center;
}

.step-line {
  flex: 1;
  height: 2px;
  margin: 0 8px;
  margin-bottom: 22px;
}

.step-line.dotted {
  border-top: 2px dashed var(--border, #cbd5e1);
}

.step-line.solid {
  background-color: var(--accent, #e52e2e);
}

.order-items-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 15px;
  padding-top: 15px;
  border-top: 1px dashed var(--border, #e2e8f0);
}

.order-item-pill {
  background: var(--bg-input, #f1f5f9);
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 0.8rem;
  border: 1px solid var(--border, #e2e8f0);
}

/* GRID DE PRODUCTOS DE FAVORITOS E HISTORIAL */
.products-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 20px;
}

/* MODAL EDITAR PERFIL */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.65);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  padding: 20px;
}

.modal-card {
  background: var(--bg-panel, #ffffff);
  color: var(--text-main, #0f172a);
  border-radius: 12px;
  padding: 28px;
  max-width: 480px;
  width: 100%;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
}

.modal-card h3 {
  margin: 0 0 20px 0;
  font-size: 1.25rem;
  font-weight: 800;
}

.form-group {
  margin-bottom: 16px;
}

.form-label {
  display: block;
  font-size: 0.85rem;
  font-weight: 700;
  margin-bottom: 6px;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.input-text,
.input-textarea {
  width: 100%;
  padding: 10px;
  border: 1px solid var(--border, #cbd5e1);
  border-radius: 6px;
  background: var(--bg-input, #f8fafc);
  color: var(--text-main, #0f172a);
  font-size: 0.9rem;
  box-sizing: border-box;
}

.photo-upload-box {
  display: flex;
  align-items: center;
  gap: 15px;
}

.preview-avatar {
  width: 55px;
  height: 55px;
  border-radius: 50%;
  background: var(--bg-input, #f1f5f9);
  border: 1px solid var(--border, #cbd5e1);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  font-size: 1.8rem;
  flex-shrink: 0;
}

.preview-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.upload-controls {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.btn-remove-photo {
  background: none;
  border: none;
  color: #dc2626;
  font-size: 0.78rem;
  font-weight: 700;
  cursor: pointer;
  padding: 0;
  text-align: left;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 24px;
}

.btn-modal-cancel,
.btn-modal-save {
  padding: 10px 18px;
  border-radius: 8px;
  font-weight: 700;
  font-size: 0.9rem;
  cursor: pointer;
  border: none;
}

.btn-modal-cancel {
  background: var(--bg-input, #e2e8f0);
  color: var(--text-main, #0f172a);
}

.btn-modal-save {
  background: var(--accent, #e52e2e);
  color: #ffffff;
}

@media (max-width: 650px) {
  .step-title {
    display: none;
  }
  .form-row {
    grid-template-columns: 1fr;
  }
}
</style>
