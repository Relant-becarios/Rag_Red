// Cliente del asistente IA (RAG Relant: app/marketplace_api.py)
//
// VITE_RAG_API_URL -> URL del servidor RAG en producción (p. ej. https://api.relant.com.mx).
// En desarrollo (npm run dev) se deja vacía: las llamadas van a /marketplace/* del mismo
// origen y Vite las reenvía al RAG (vite.config.ts, RAG_DEV_TARGET).

const configured = String(import.meta.env.VITE_RAG_API_URL || '').trim()
if (configured && !/^https?:\/\/[^\s[\]()]+$/i.test(configured)) {
  console.error(`VITE_RAG_API_URL no es una URL válida: "${configured}"`)
}

// Vacía (recomendado) -> mismo origen: el navegador llama a /marketplace/* de la tienda y
// lo reenvía el proxy de Vite (dev) o de Server.js (IIS) al RAG. Así no hay CORS ni puertos
// internos (127.0.0.1:8021) expuestos al navegador del cliente.
export const RAG_API_URL: string = (
  /^https?:\/\/[^\s[\]()]+$/i.test(configured) ? configured : ''
).replace(/\/+$/, '')

export interface AssistantProduct {
  sku: string
  key: string
  name: string
  category: string
  price: number
  currency: string
  stock: number
  in_stock: boolean
  image: string
  attributes: Record<string, string>
  equivalents: string
  url: string
  match: 'recommended' | 'related'
}

export interface AssistantLink {
  id?: string
  title?: string
  label?: string
  kind?: string
  url: string
  cited?: boolean
}

export interface AssistantReply {
  answer: string
  products: AssistantProduct[]
  sources: AssistantLink[]
  drawings: AssistantLink[]
  downloads: AssistantLink[]
  conversation_id: number
  conversation_token: string
  message_id: number
  route: string
  rag_route: string
  seconds: number
}

export interface ShopperContext {
  viewing_sku?: string
  cart_skus?: string[]
  page?: string
}

export interface AssistantHealth {
  ok: boolean
  catalog: { enabled: boolean; products: number; error: string }
  rag: { enabled: boolean; chunks?: number; error?: string }
}

/** URL relativa del RAG (/marketplace/files/...) -> absoluta. */
export const ragUrl = (url: string): string => {
  if (!url) return ''
  if (/^https?:\/\//i.test(url)) return url
  return `${RAG_API_URL}${url.startsWith('/') ? '' : '/'}${url}`
}

export class AssistantError extends Error {
  status: number
  constructor(message: string, status = 0) {
    super(message)
    this.status = status
  }
}

export const askAssistant = async (body: {
  message: string
  conversation_id?: number | null
  conversation_token?: string | null
  context?: ShopperContext
}): Promise<AssistantReply> => {
  let res: Response
  try {
    res = await fetch(`${RAG_API_URL}/marketplace/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new AssistantError(
      'No se pudo conectar con el asistente. Revisa tu conexión e inténtalo de nuevo.',
    )
  }
  let data: unknown = null
  try {
    data = await res.json()
  } catch {
    /* respuesta sin JSON */
  }
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail
    const msg =
      typeof detail === 'string'
        ? detail
        : res.status === 429
          ? 'Demasiadas preguntas seguidas. Espera un momento.'
          : 'El asistente no está disponible en este momento.'
    throw new AssistantError(msg, res.status)
  }
  return data as AssistantReply
}

export const assistantHealth = async (): Promise<AssistantHealth | null> => {
  try {
    const res = await fetch(`${RAG_API_URL}/marketplace/health`)
    if (!res.ok) return null
    return (await res.json()) as AssistantHealth
  } catch {
    return null
  }
}
