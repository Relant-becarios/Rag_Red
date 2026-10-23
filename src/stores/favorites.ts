import { defineStore } from 'pinia'
import { ref } from 'vue'
import { db } from '@/firebase'
import { ref as dbRef, get, set, remove } from 'firebase/database'
import { getAuth, onAuthStateChanged } from 'firebase/auth'
import type { Producto } from '@/api/inventory'

const INVALID_KEY_REGEX = new RegExp('[.#$\\[\\]/]', 'g')

// Sanitiza recursivamente y omite llaves vacías
const sanitizeForFirebase = (data: unknown): unknown => {
  if (data === null || data === undefined) return null
  if (typeof data !== 'object') return data

  if (Array.isArray(data)) {
    return data.map(sanitizeForFirebase)
  }

  const obj = data as Record<string, unknown>
  const sanitized: Record<string, unknown> = {}

  for (const key of Object.keys(obj)) {
    const val = obj[key]
    if (val !== undefined) {
      const cleanKey = key.replace(INVALID_KEY_REGEX, '_').trim()
      // Firebase NO permite llaves vacías. Si la llave está en blanco, se ignora.
      if (cleanKey !== '') {
        sanitized[cleanKey] = sanitizeForFirebase(val)
      }
    }
  }
  return sanitized
}

export const useFavoritesStore = defineStore('favorites', () => {
  const favoritos = ref<Producto[]>([])

  const obtenerKeyValida = (producto: Producto | string): string => {
    if (typeof producto === 'string') {
      return producto.replace(INVALID_KEY_REGEX, '_').trim()
    }
    const rawId = producto.id || producto.SKU || producto.Producto || 'item_sin_id'
    return String(rawId).replace(INVALID_KEY_REGEX, '_').trim()
  }

  const cargarFavoritos = async () => {
    const auth = getAuth()
    const user = auth.currentUser

    if (!user) {
      favoritos.value = []
      return
    }

    try {
      const snap = await get(dbRef(db, `favoritos/${user.uid}`))
      if (snap.exists()) {
        favoritos.value = Object.values(snap.val()) as Producto[]
      } else {
        favoritos.value = []
      }
    } catch (e) {
      console.error('[Firebase Error] Carga de favoritos fallida:', e)
    }
  }

  onAuthStateChanged(getAuth(), (user) => {
    if (user) {
      cargarFavoritos()
    } else {
      favoritos.value = []
    }
  })

  const toggleFavorito = async (producto: Producto) => {
    const auth = getAuth()
    const user = auth.currentUser

    if (!user) {
      alert('Debes iniciar sesión para guardar productos en tus favoritos.')
      return
    }

    const idClave = obtenerKeyValida(producto)
    const productoLimpio = sanitizeForFirebase(producto)

    const index = favoritos.value.findIndex((item) => obtenerKeyValida(item) === idClave)

    try {
      if (index > -1) {
        await remove(dbRef(db, `favoritos/${user.uid}/${idClave}`))
        favoritos.value.splice(index, 1)
      } else {
        await set(dbRef(db, `favoritos/${user.uid}/${idClave}`), productoLimpio)
        favoritos.value.push(producto)
      }
    } catch (e: unknown) {
      const mensaje = e instanceof Error ? e.message : String(e)
      console.error('[Firebase Error Detalles]:', e)
      alert(`Fallo en Firebase: ${mensaje}`)
    }
  }

  const esFavorito = (target: Producto | string): boolean => {
    const targetId = obtenerKeyValida(target)
    return favoritos.value.some((item) => obtenerKeyValida(item) === targetId)
  }

  return { favoritos, cargarFavoritos, toggleFavorito, esFavorito }
})
