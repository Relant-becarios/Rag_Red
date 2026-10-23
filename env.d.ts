/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** URL del servidor RAG (asistente IA), p. ej. https://api.relant.com.mx */
  readonly VITE_RAG_API_URL?: string
}
