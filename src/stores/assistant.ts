// Estado del asistente IA, compartido por el panel lateral (ChatPanel) y la vista
// /asistente: la misma conversación sigue al cliente por toda la tienda.
import { defineStore } from 'pinia'
import { ref } from 'vue'
import router from '@/router'
import {
  askAssistant,
  assistantHealth,
  AssistantError,
  type AssistantReply,
  type ShopperContext,
} from '@/api/assistant'
import { useMarketStore, type ProductoExtendido } from '@/stores/market'
import { useCartStore } from '@/stores/cart'

export interface AssistantMessage {
  role: 'user' | 'assistant' | 'error'
  content: string
  reply?: AssistantReply
  at: number
}

const STORAGE_KEY = 'relant_asistente_conversacion'
const VIEW_WINDOW_MS = 15 * 60 * 1000 // un producto visto hace <15 min cuenta como contexto

const skuDe = (p: unknown): string => {
  const x = (p || {}) as ProductoExtendido
  return String(x.id || x.ID || x.SKU || x['no. De parte'] || x.NO_DE_PARTE || '').trim()
}

interface Guardado {
  messages: AssistantMessage[]
  conversationId: number | null
  conversationToken: string | null
}

const leer = (): Guardado | null => {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Guardado) : null
  } catch {
    return null
  }
}

export const useAssistantStore = defineStore('assistant', () => {
  const previo = leer()
  const messages = ref<AssistantMessage[]>(previo?.messages || [])
  const conversationId = ref<number | null>(previo?.conversationId ?? null)
  const conversationToken = ref<string | null>(previo?.conversationToken ?? null)
  const sending = ref(false)
  const online = ref<boolean | null>(null)
  const statusText = ref('conectando…')

  const guardar = () => {
    try {
      sessionStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          messages: messages.value.slice(-40),
          conversationId: conversationId.value,
          conversationToken: conversationToken.value,
        }),
      )
    } catch {
      /* modo privado / almacenamiento lleno: la conversación vive en memoria */
    }
  }

  /** Qué está viendo el cliente y qué tiene en el carrito: el asistente lo usa para
   *  preguntas como "¿qué aguja le queda a esta válvula?". */
  const contexto = (): ShopperContext => {
    const market = useMarketStore()
    const cart = useCartStore()
    let viendo = skuDe(market.productoSeleccionado)
    if (!viendo) {
      const ultimo = market.historial[0]
      if (ultimo && Date.now() - (ultimo.vistoEn || 0) < VIEW_WINDOW_MS) viendo = skuDe(ultimo)
    }
    return {
      viewing_sku: viendo,
      cart_skus: cart.items.map((i) => String(i.id)).slice(0, 30),
      page: String(router.currentRoute.value?.fullPath || window.location.pathname || '').slice(0, 120),
    }
  }

  const enviar = async (texto: string) => {
    const q = texto.trim()
    if (!q || sending.value) return
    messages.value.push({ role: 'user', content: q, at: Date.now() })
    sending.value = true
    guardar()
    try {
      const reply = await askAssistant({
        message: q,
        conversation_id: conversationId.value,
        conversation_token: conversationToken.value,
        context: contexto(),
      })
      conversationId.value = reply.conversation_id
      conversationToken.value = reply.conversation_token
      messages.value.push({ role: 'assistant', content: reply.answer, reply, at: Date.now() })
      online.value = true
    } catch (e) {
      const msg = e instanceof AssistantError ? e.message : 'Ocurrió un error inesperado.'
      messages.value.push({ role: 'error', content: msg, at: Date.now() })
      if (e instanceof AssistantError && e.status === 0) online.value = false
    } finally {
      sending.value = false
      guardar()
    }
  }

  const nuevaConversacion = () => {
    messages.value = []
    conversationId.value = null
    conversationToken.value = null
    guardar()
  }

  const verificarServidor = async () => {
    const h = await assistantHealth()
    online.value = !!h?.ok
    statusText.value = !h
      ? 'sin conexión al asistente'
      : h.ok
        ? `en línea · ${h.catalog.products} productos`
        : 'asistente con problemas'
  }

  return {
    messages,
    sending,
    online,
    statusText,
    conversationId,
    enviar,
    nuevaConversacion,
    verificarServidor,
  }
})
