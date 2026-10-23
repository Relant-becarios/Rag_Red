<template>
  <div class="welcome-container">
    <NavBar @toggle-cart="$emit('toggle-cart')" />

    <main class="main-content">
      <section class="modules-section">
        <h2 class="section-title">Módulos del Sistema</h2>

        <div class="cards-grid">
          <div class="module-card" @click="goTo('/catalogo')">
            <div class="icon-svg">
              <svg
                width="40"
                height="40"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20" />
              </svg>
            </div>
            <h3>Catálogo de Partes</h3>
            <p>Acceso a inventario, refacciones y especificaciones técnicas.</p>
          </div>

          <div class="module-card" @click="manejarClickMaquina">
            <div class="icon-svg" style="position: relative">
              <svg
                width="40"
                height="40"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <rect x="4" y="2" width="16" height="20" rx="2" ry="2" />
                <path d="M8 6h.01" />
                <path d="M16 6h.01" />
                <path d="M12 6h.01" />
                <path d="M12 10h.01" />
                <path d="M12 14h.01" />
                <path d="M16 10h.01" />
                <path d="M16 14h.01" />
                <path d="M8 10h.01" />
                <path d="M8 14h.01" />
                <path d="M8 18h8" />
              </svg>
              <span v-if="!esPremium" class="lock-badge">🔒</span>
            </div>
            <h3>Gestión de Máquinas</h3>
            <p>Control y surtido automatizado de equipos expendedores.</p>
          </div>

          <div class="module-card" @click="goTo('/concepto')">
            <div class="diagram-container">
              <div class="node top">NÚCLEO</div>
              <div class="lines-horizontal">
                <div class="node side">MOD 1</div>
                <div class="center-hub"></div>
                <div class="node side">MOD 2</div>
              </div>
              <div class="node bottom">BASE</div>
            </div>
            <h3>Configurador</h3>
            <p>Diseño y ensamblaje paramétrico de válvulas a medida.</p>
          </div>

          <div class="module-card" @click="goTo('/ia-foto')">
            <div class="icon-svg">
              <svg
                width="40"
                height="40"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path
                  d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"
                />
                <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
                <line x1="12" y1="22.08" x2="12" y2="12" />
              </svg>
            </div>
            <h3>Visión Artificial</h3>
            <p>Reconocimiento y análisis de componentes industriales.</p>
          </div>
        </div>
      </section>

      <section class="news-section">
        <h2 class="section-title">Boletín Técnico</h2>
        <div class="news-feed">
          <article class="news-item">
            <h4>Actualización de Inventario</h4>
            <p>
              Se han integrado al catálogo las nuevas series AXDV para dispensado de alta precisión.
              Consulte las especificaciones en la sección correspondiente.
            </p>
          </article>
          <article class="news-item">
            <h4>Mejoras en Visión Artificial</h4>
            <p>
              El algoritmo de reconocimiento ha sido calibrado para detectar micro-componentes con
              un 30% más de fiabilidad bajo condiciones complejas.
            </p>
          </article>
        </div>
      </section>
    </main>

    <!-- MODAL DE ACCESO -->
    <div v-if="mostrarModalAcceso" class="modal-wrapper">
      <div class="modal-backdrop" @click="cerrarModalAcceso"></div>
      <div class="access-content">
        <div class="access-header">
          <h2 class="access-title">🔒 Acceso Restringido</h2>
          <span @click="cerrarModalAcceso" class="close-icon">✕</span>
        </div>

        <div class="access-body">
          <p class="access-desc">
            Para acceder y operar este equipo, registre sus credenciales y la clave de la máquina.
          </p>

          <div class="input-group">
            <label>Nombre Completo</label>
            <input type="text" v-model="nombreIngresado" placeholder="Ej. Juan Pérez" />
          </div>
          <div class="input-group">
            <label>Correo Electrónico</label>
            <input type="email" v-model="emailIngresado" placeholder="juan@empresa.com" />
          </div>
          <div class="input-group">
            <label>Clave de Acceso Secreta</label>
            <input
              type="password"
              v-model="claveIngresada"
              placeholder="Ingrese su clave..."
              @keyup.enter="verificarClave"
            />
          </div>

          <p v-if="errorClave" class="error-msg">{{ mensajeErrorApi }}</p>

          <button class="btn-verify" @click="verificarClave">REGISTRAR Y VERIFICAR</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import NavBar from '@/components/NavBar.vue'

defineEmits(['toggle-cart'])

const router = useRouter()
const authStore = useAuthStore()

const goTo = (ruta: string) => {
  router.push(ruta)
}

const esPremium = computed(() => {
  if (!authStore.usuarioActual) return false
  const usuario = authStore.usuarioActual as { rol?: string }
  const rol = usuario.rol?.toLowerCase()
  return rol === 'premium' || rol === 'admin'
})

const mostrarModalAcceso = ref(false)
const nombreIngresado = ref('')
const emailIngresado = ref('')
const claveIngresada = ref('')
const errorClave = ref(false)
const mensajeErrorApi = ref('')

const manejarClickMaquina = () => {
  if (esPremium.value) {
    goTo('/maquina/acceso-admin')
  } else {
    mostrarModalAcceso.value = true
  }
}

const cerrarModalAcceso = () => {
  mostrarModalAcceso.value = false
  nombreIngresado.value = ''
  emailIngresado.value = ''
  claveIngresada.value = ''
  errorClave.value = false
  mensajeErrorApi.value = ''
}

const verificarClave = async () => {
  errorClave.value = false
  mensajeErrorApi.value = ''

  if (!claveIngresada.value || !nombreIngresado.value || !emailIngresado.value) {
    errorClave.value = true
    mensajeErrorApi.value = 'Por favor, llene todos los campos.'
    return
  }

  try {
    const response = await fetch('http://localhost:3000/api/validar-licencia', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clave: claveIngresada.value,
        emailUsuario: emailIngresado.value,
        nombreUsuario: nombreIngresado.value,
      }),
    })

    const data = await response.json()

    if (response.ok && data.success) {
      cerrarModalAcceso()
      goTo(`/maquina/${data.maquinaId}`)
    } else {
      errorClave.value = true
      mensajeErrorApi.value = data.error || 'Clave incorrecta o límite de usuarios alcanzado.'
    }
  } catch (error) {
    console.error(error)
    errorClave.value = true
    mensajeErrorApi.value = 'Error conectando al servidor. Verifique su conexión.'
  }
}
</script>

<style scoped>
.welcome-container {
  min-height: 100vh;
  background: var(--bg-main);
  color: var(--text-main);
}

.main-content {
  padding-top: 30px;
}

.modules-section,
.news-section {
  padding: 0 5%;
  max-width: 1200px;
  margin: 0 auto 50px auto;
}

.section-title {
  font-size: 1.3rem;
  margin-bottom: 25px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 1px;
}

/* Grid de Módulos */
.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 20px;
}

.module-card {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 30px 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s ease;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.module-card:hover {
  border-color: var(--accent);
  background: rgba(255, 0, 0, 0.02);
}

.icon-svg {
  color: var(--accent);
  margin-bottom: 15px;
}

.lock-badge {
  position: absolute;
  top: -10px;
  right: -15px;
  font-size: 1.2rem;
  background: var(--bg-panel);
  border-radius: 50%;
  padding: 2px;
}

.module-card h3 {
  margin: 10px 0;
  font-size: 1.1rem;
  color: var(--text-main);
}

.module-card p {
  font-size: 0.85rem;
  color: var(--text-muted);
  margin: 0;
  line-height: 1.4;
}

/* Diagrama de Concepto */
.diagram-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 15px;
  width: 100%;
}

.node {
  background: var(--bg-input);
  font-size: 0.65rem;
  color: var(--text-muted);
  font-weight: bold;
  padding: 4px 10px;
  border-radius: 3px;
  border: 1px solid var(--border);
  z-index: 2;
  letter-spacing: 1px;
}

.lines-horizontal {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  margin: 12px 0;
  position: relative;
}

.center-hub {
  width: 12px;
  height: 12px;
  background: var(--accent);
  border-radius: 50%;
  margin: 0 15px;
  position: relative;
}

.center-hub::before,
.center-hub::after {
  content: '';
  position: absolute;
  background: var(--accent);
  z-index: 1;
}

.center-hub::before {
  width: 1px;
  height: 60px;
  top: -24px;
  left: 5px;
}

.center-hub::after {
  width: 90px;
  height: 1px;
  top: 5px;
  left: -39px;
}

/* Sección de Noticias */
.news-section {
  background: transparent;
}

.news-feed {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.news-item {
  padding: 20px 25px;
  border-left: 3px solid var(--accent);
  background: var(--bg-panel);
  border-radius: 0 6px 6px 0;
  border-top: 1px solid var(--border);
  border-right: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}

.news-item h4 {
  margin: 0 0 10px 0;
  font-size: 1rem;
  color: var(--text-main);
}

.news-item p {
  margin: 0;
  font-size: 0.9rem;
  color: var(--text-muted);
  line-height: 1.5;
}

/* Modal de Acceso Restringido */
.modal-wrapper {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 25000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.modal-backdrop {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.85);
  z-index: 1;
}

.access-content {
  position: relative;
  z-index: 2;
  background: var(--bg-panel);
  width: 100%;
  max-width: 420px;
  padding: 30px;
  border-radius: 12px;
  border: 1px solid var(--border);
  color: var(--text-main);
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.8);
}

.access-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.access-title {
  color: var(--accent);
  margin: 0;
  font-size: 1.5rem;
}

.close-icon {
  cursor: pointer;
  font-size: 20px;
  color: var(--text-muted);
}

.close-icon:hover {
  color: var(--text-main);
}

.access-desc {
  font-size: 0.9rem;
  color: var(--text-muted);
  margin-bottom: 20px;
}

.input-group {
  margin-bottom: 15px;
}

.input-group label {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 5px;
  display: block;
}

.input-group input {
  width: 100%;
  padding: 12px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-main);
  outline: none;
  box-sizing: border-box;
}

.btn-verify {
  background: var(--accent);
  color: white;
  border: none;
  padding: 12px;
  border-radius: 6px;
  font-weight: bold;
  cursor: pointer;
  transition: 0.2s;
  width: 100%;
  margin-top: 5px;
}

.btn-verify:hover {
  background: var(--accent-hover);
}

.error-msg {
  color: #ff4444;
  font-size: 13px;
  margin-top: -5px;
  margin-bottom: 10px;
}
</style>
