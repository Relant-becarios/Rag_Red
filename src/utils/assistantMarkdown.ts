// Markdown mínimo y SEGURO para las respuestas del asistente: primero se escapa todo el
// HTML, después se aplican **negritas**, `código`, listas, links y las etiquetas
// [mp:SKU] (productos de la tienda) y [id] (fuentes técnicas).
import { ragUrl, type AssistantLink } from '@/api/assistant'

const esc = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

export interface RenderOptions {
  productName: (sku: string) => string | null // null = no está en la tienda
  sources: AssistantLink[]
}

const inline = (raw: string, opt: RenderOptions, nums: Map<string, number>): string => {
  let t = esc(raw)
  // links markdown [texto](url): sólo http(s) o rutas del RAG
  t = t.replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/)[^)\s]+)\)/g, (_m, txt: string, url: string) => {
    const href = esc(ragUrl(url.replace(/&amp;/g, '&')))
    return `<a href="${href}" target="_blank" rel="noopener">${txt}</a>`
  })
  // productos de la tienda
  t = t.replace(/\[mp:\s*([^\]]+?)\s*\]/gi, (_m, sku: string) => {
    const name = opt.productName(sku)
    if (name === null) return ''
    return `<button type="button" class="sku-chip" data-sku="${esc(sku)}" title="${esc(name)}">${esc(sku)}</button>`
  })
  // [id] de fuentes técnicas -> nota numerada; ids internos sin fuente pública -> fuera
  t = t.replace(/\[([^\]\s]{3,160})\]/g, (m, id: string) => {
    const src = opt.sources.find((s) => s.id === id)
    if (src) {
      if (!nums.has(id)) nums.set(id, nums.size + 1)
      return `<sup><a class="src-ref" href="${esc(ragUrl(src.url))}" target="_blank" rel="noopener" title="${esc(src.title || id)}">${nums.get(id)}</a></sup>`
    }
    return /[._:/]|chunk|row\d/i.test(id) ? '' : m
  })
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>')
  return t.replace(/ {2,}/g, ' ')
}

export const renderAnswer = (md: string, opt: RenderOptions): string => {
  const nums = new Map<string, number>()
  const out: string[] = []
  let list: 'ul' | 'ol' | null = null
  const close = () => {
    if (list) out.push(`</${list}>`)
    list = null
  }
  for (const line of (md || '').replace(/\r/g, '').split('\n')) {
    const ul = line.match(/^\s*[-*•]\s+(.*)$/)
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/)
    if (ul || ol) {
      const kind = ul ? 'ul' : 'ol'
      if (list !== kind) {
        close()
        out.push(`<${kind}>`)
        list = kind
      }
      out.push(`<li>${inline((ul || ol)![1] ?? '', opt, nums)}</li>`)
      continue
    }
    close()
    const h = line.match(/^\s*#{1,6}\s+(.*)$/)
    if (h) out.push(`<p><strong>${inline(h[1] ?? '', opt, nums)}</strong></p>`)
    else if (line.trim()) out.push(`<p>${inline(line, opt, nums)}</p>`)
  }
  close()
  return out.join('')
}
