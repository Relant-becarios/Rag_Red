import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { db, auth } from '@/firebase'
import { ref as dbRef, set, get } from 'firebase/database'
import { onAuthStateChanged } from 'firebase/auth'
import { useMarketStore, type ProductoExtendido } from '@/stores/market'

export interface ItemCarrito {
  id: string
  cant: number
}

const STORAGE_KEY = 'relant_cart_items'

const normalizarCarrito = (val: unknown): ItemCarrito[] => {
  if (!val) return []
  if (Array.isArray(val)) {
    return val.filter((item) => item && typeof item === 'object' && (item as ItemCarrito).id)
  }
  if (typeof val === 'object') {
    return Object.values(val as Record<string, ItemCarrito>).filter(
      (item) => item && typeof item === 'object' && item.id,
    )
  }
  return []
}

export const useCartStore = defineStore('cart', () => {
  const items = ref<ItemCarrito[]>(
    (() => {
      try {
        const guardado = localStorage.getItem(STORAGE_KEY)
        return guardado ? JSON.parse(guardado) : []
      } catch (e) {
        console.error('Error leyendo localStorage:', e)
        return []
      }
    })(),
  )

  const usuarioId = ref<string | null>(null)
  const cargandoSincronizacion = ref<boolean>(false)

  watch(
    items,
    async (nuevosItems) => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(nuevosItems))

      if (usuarioId.value && !cargandoSincronizacion.value) {
        try {
          await set(dbRef(db, `carritos/${usuarioId.value}`), nuevosItems)
        } catch (error) {
          console.error('Error al sincronizar con Firebase:', error)
        }
      }
    },
    { deep: true },
  )

  onAuthStateChanged(auth, async (user) => {
    if (user) {
      usuarioId.value = user.uid
      cargandoSincronizacion.value = true

      try {
        const snapshot = await get(dbRef(db, `carritos/${user.uid}`))
        const carritoNube = normalizarCarrito(snapshot.val())

        if (carritoNube.length > 0) {
          const mapa = new Map<string, number>()

          carritoNube.forEach((item) => mapa.set(item.id, item.cant))

          items.value.forEach((item) => {
            const cantExistente = mapa.get(item.id) || 0
            mapa.set(item.id, Math.max(cantExistente, item.cant))
          })

          items.value = Array.from(mapa.entries()).map(([id, cant]) => ({ id, cant }))
        } else if (items.value.length > 0) {
          await set(dbRef(db, `carritos/${user.uid}`), items.value)
        }
      } catch (error) {
        console.error('Error al sincronizar con la nube:', error)
      } finally {
        cargandoSincronizacion.value = false
        localStorage.setItem(STORAGE_KEY, JSON.stringify(items.value))
      }
    } else {
      usuarioId.value = null
    }
  })

  const totalItems = computed(() => {
    return items.value.reduce((total, item) => total + item.cant, 0)
  })

  // Búsqueda global de stock si la vista no lo provee explícitamente
  function obtenerStockReal(idProducto: string, stockParam?: number): number {
    if (typeof stockParam === 'number' && stockParam >= 0) {
      return stockParam
    }

    const marketStore = useMarketStore()
    const targetKey = String(idProducto).trim().toLowerCase()

    const productoEnVivo = marketStore.productos.find((p) => {
      const pExt = p as ProductoExtendido
      const key = String(
        pExt.id || pExt.ID || pExt.SKU || pExt['no. De parte'] || pExt.NO_DE_PARTE || '',
      )
        .trim()
        .toLowerCase()
      return key === targetKey
    })

    if (productoEnVivo) {
      const pExt = productoEnVivo as ProductoExtendido
      const val = pExt.Stock
      return typeof val === 'number' ? val : parseInt(String(val || 0), 10) || 0
    }

    return 99999 // Fallback si no está cargado el catálogo
  }

  function agregarProducto(idProducto: string, stockMaximo?: number, cantidadAgregar = 1) {
    if (!idProducto || idProducto === 'undefined') return

    const targetId = String(idProducto).trim()
    const stockReal = obtenerStockReal(targetId, stockMaximo)

    if (stockReal <= 0) return

    const existe = items.value.find(
      (item) => String(item.id).trim().toLowerCase() === targetId.toLowerCase(),
    )

    if (existe) {
      const nuevaCantidad = existe.cant + cantidadAgregar
      existe.cant = Math.min(nuevaCantidad, stockReal)
    } else {
      items.value.push({
        id: targetId,
        cant: Math.min(cantidadAgregar, stockReal),
      })
    }
  }

  function quitarProducto(idProducto: string) {
    const targetId = String(idProducto).trim().toLowerCase()
    items.value = items.value.filter((item) => String(item.id).trim().toLowerCase() !== targetId)
  }

  function actualizarCantidad(idProducto: string, nuevaCantidad: number, stockMaximo?: number) {
    const targetId = String(idProducto).trim().toLowerCase()
    const item = items.value.find((item) => String(item.id).trim().toLowerCase() === targetId)

    if (item && nuevaCantidad > 0) {
      const stockReal = obtenerStockReal(targetId, stockMaximo)
      item.cant = Math.min(nuevaCantidad, stockReal)
    }
  }

  function vaciarCarrito() {
    items.value = []
  }

  return {
    items,
    totalItems,
    agregarProducto,
    quitarProducto,
    actualizarCantidad,
    vaciarCarrito,
  }
})
