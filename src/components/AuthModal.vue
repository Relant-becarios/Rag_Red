<template>
  <div v-if="uiStore.isAuthModalOpen" class="auth-overlay" @click.self="uiStore.toggleAuthModal">
    <div class="auth-card">
      <div class="auth-header">
        <h2>{{ esRegistro ? 'Crear Cuenta' : 'Iniciar Sesión' }}</h2>
        <span class="close-btn" @click="uiStore.toggleAuthModal">✕</span>
      </div>

      <form @submit.prevent="handleSubmit" class="auth-form">
        <!-- Campos adicionales en creación de cuenta -->
        <template v-if="esRegistro">
          <div class="form-group">
            <label>Nombre</label>
            <input type="text" v-model="nombre" placeholder="Tu nombre" required />
          </div>
          <div class="form-group">
            <label>Apellidos</label>
            <input type="text" v-model="apellidos" placeholder="Tus apellidos" required />
          </div>
        </template>

        <div class="form-group">
          <label>Correo Electrónico</label>
          <input type="email" v-model="email" placeholder="correo@ejemplo.com" required />
        </div>

        <div class="form-group">
          <label>Contraseña</label>
          <input type="password" v-model="password" placeholder="••••••••" required />
        </div>

        <p v-if="errorMsg" class="error-msg">{{ errorMsg }}</p>

        <button type="submit" class="btn-submit" :disabled="cargando">
          {{ cargando ? 'Procesando...' : esRegistro ? 'REGISTRARSE' : 'ENTRAR' }}
        </button>
      </form>

      <div class="divider"><span>O CONTINÚA CON</span></div>

      <!-- Botón Google -->
      <button type="button" class="btn-google" @click="loginGoogle" :disabled="cargando">
        <svg viewBox="0 0 24 24" width="18" height="18">
          <path
            fill="#4285F4"
            d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
          />
          <path
            fill="#34A853"
            d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
          />
          <path
            fill="#FBBC05"
            d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
          />
          <path
            fill="#EA4335"
            d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
          />
        </svg>
        Google
      </button>

      <div class="auth-footer">
        <span>{{ esRegistro ? '¿Ya tienes cuenta?' : '¿No tienes cuenta?' }}</span>
        <button type="button" class="btn-toggle" @click="esRegistro = !esRegistro">
          {{ esRegistro ? 'Inicia sesión' : 'Regístrate aquí' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useUiStore } from '@/stores/ui'
import { useAuthStore } from '@/stores/auth'

const uiStore = useUiStore()
const authStore = useAuthStore()

const esRegistro = ref(false)
const nombre = ref('')
const apellidos = ref('')
const email = ref('')
const password = ref('')
const errorMsg = ref('')
const cargando = ref(false)

const getErrorMessage = (error: unknown, fallback: string) => {
  if (error instanceof Error) return error.message

  if (typeof error === 'object' && error !== null && 'message' in error) {
    const message = error.message
    if (typeof message === 'string' && message.trim()) {
      return message
    }
  }

  return fallback
}

const handleSubmit = async () => {
  errorMsg.value = ''
  cargando.value = true
  try {
    if (esRegistro.value) {
      await authStore.registrar(email.value, password.value, nombre.value, apellidos.value)
    } else {
      await authStore.iniciarSesion(email.value, password.value)
    }
    uiStore.toggleAuthModal()
    limpiarFormulario()
  } catch (error: unknown) {
    errorMsg.value = getErrorMessage(error, 'Error al procesar la solicitud')
  } finally {
    cargando.value = false
  }
}

const loginGoogle = async () => {
  errorMsg.value = ''
  cargando.value = true
  try {
    await authStore.iniciarConGoogle()
    uiStore.toggleAuthModal()
    limpiarFormulario()
  } catch (error: unknown) {
    errorMsg.value = getErrorMessage(error, 'Error con Google Sign-In')
  } finally {
    cargando.value = false
  }
}

const limpiarFormulario = () => {
  nombre.value = ''
  apellidos.value = ''
  email.value = ''
  password.value = ''
}
</script>

<style scoped>
.auth-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.75);
  z-index: 35000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.auth-card {
  background: var(--bg-panel, #ffffff);
  width: 100%;
  max-width: 420px;
  border-radius: 12px;
  padding: 30px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
  border: 1px solid var(--border);
}
.auth-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.auth-header h2 {
  color: var(--accent, #d32f2f);
  margin: 0;
  font-size: 1.5rem;
  font-weight: 800;
}
.close-btn {
  font-size: 1.2rem;
  cursor: pointer;
  color: var(--text-muted);
}

.auth-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.form-group {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.form-group label {
  font-size: 0.82rem;
  color: var(--text-muted);
  font-weight: 600;
}
.form-group input {
  background: var(--bg-input);
  border: 1px solid var(--border);
  padding: 12px;
  border-radius: 6px;
  color: var(--text-main);
  font-size: 0.95rem;
  outline: none;
}

.error-msg {
  color: #ef4444;
  font-size: 0.8rem;
  margin: 0;
}
.btn-submit {
  background: var(--accent, #d32f2f);
  color: white;
  border: none;
  padding: 14px;
  border-radius: 6px;
  font-weight: 800;
  cursor: pointer;
}

.divider {
  text-align: center;
  border-bottom: 1px solid var(--border);
  line-height: 0.1em;
  margin: 20px 0;
}
.divider span {
  background: var(--bg-panel);
  padding: 0 10px;
  font-size: 0.7rem;
  color: var(--text-muted);
  font-weight: bold;
}

.btn-google {
  width: 100%;
  background: var(--bg-input);
  border: 1px solid var(--border);
  padding: 10px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  font-weight: bold;
  color: var(--text-main);
  cursor: pointer;
}

.auth-footer {
  margin-top: 20px;
  text-align: center;
  font-size: 0.85rem;
  color: var(--text-muted);
  display: flex;
  justify-content: center;
  gap: 6px;
}
.btn-toggle {
  background: transparent;
  border: none;
  color: var(--accent);
  font-weight: bold;
  cursor: pointer;
}
</style>
