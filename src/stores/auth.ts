import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { auth, db } from '@/firebase'
import {
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  signOut,
  onAuthStateChanged,
  GoogleAuthProvider,
  signInWithPopup,
  updateProfile,
  type User,
} from 'firebase/auth'
import { ref as dbRef, set, get } from 'firebase/database'

export interface PerfilUsuario {
  uid: string
  email: string
  nombre: string
  apellidos: string
  rol?: string
}

export const useAuthStore = defineStore('auth', () => {
  const usuarioActual = ref<User | null>(null)
  const perfil = ref<PerfilUsuario | null>(null)

  // Promesa para controlar cuándo Firebase termina de cargar el estado inicial
  let resolveAuthReady: () => void
  const authReady = new Promise<void>((resolve) => {
    resolveAuthReady = resolve
  })

  const esAdmin = computed(() => {
    return perfil.value?.rol === 'admin' || perfil.value?.rol === 'ADMIN'
  })

  // Escuchar estado de sesión y cargar perfil
  onAuthStateChanged(auth, async (user) => {
    usuarioActual.value = user
    if (user) {
      await cargarPerfil(user.uid)
    } else {
      perfil.value = null
    }
    // Notifica que los datos de autenticación y perfil ya están listos
    resolveAuthReady()
  })

  const cargarPerfil = async (uid: string) => {
    try {
      const snap = await get(dbRef(db, `usuarios/${uid}`))
      if (snap.exists()) {
        perfil.value = snap.val()
      }
    } catch (e) {
      console.error('Error cargando perfil:', e)
    }
  }

  const registrar = async (email: string, pass: string, nombre: string, apellidos: string) => {
    const res = await createUserWithEmailAndPassword(auth, email, pass)
    const nombreCompleto = `${nombre} ${apellidos}`.trim()
    await updateProfile(res.user, { displayName: nombreCompleto })

    const datosPerfil: PerfilUsuario = {
      uid: res.user.uid,
      email,
      nombre,
      apellidos,
      rol: 'user',
    }

    await set(dbRef(db, `usuarios/${res.user.uid}`), datosPerfil)
    perfil.value = datosPerfil
  }

  const iniciarSesion = async (email: string, pass: string) => {
    await signInWithEmailAndPassword(auth, email, pass)
  }

  const iniciarConGoogle = async () => {
    const provider = new GoogleAuthProvider()
    const res = await signInWithPopup(auth, provider)

    const snap = await get(dbRef(db, `usuarios/${res.user.uid}`))
    if (!snap.exists()) {
      const partesNombre = (res.user.displayName || '').split(' ')
      const nombre = partesNombre[0] || 'Usuario'
      const apellidos = partesNombre.slice(1).join(' ') || ''

      const datosPerfil: PerfilUsuario = {
        uid: res.user.uid,
        email: res.user.email || '',
        nombre,
        apellidos,
        rol: 'user',
      }
      await set(dbRef(db, `usuarios/${res.user.uid}`), datosPerfil)
      perfil.value = datosPerfil
    }
  }

  const cerrarSesion = async () => {
    await signOut(auth)
  }

  return {
    usuarioActual,
    perfil,
    esAdmin,
    authReady, // Exportamos la promesa de carga
    registrar,
    iniciarSesion,
    iniciarConGoogle,
    cerrarSesion,
  }
})
