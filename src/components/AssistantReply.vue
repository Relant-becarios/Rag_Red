<template>
  <div class="ai-reply" :class="{ compact }">
    <div class="ai-text" v-html="html" @click="onTextClick"></div>

    <!-- PRODUCTOS DE LA TIENDA (recomendados por el asistente) -->
    <div v-if="cards.length" class="ai-products">
      <div class="ai-products-title">
        {{ reply.products[0]?.match === 'related' ? 'Productos relacionados' : 'Productos recomendados' }}
      </div>
      <div class="ai-product" v-for="c in cards" :key="c.sku">
        <div class="ai-thumb" @click="ver(c)">
          <img v-if="c.image && !imgError[c.sku]" :src="c.image" :alt="c.name" loading="lazy"
            @error="imgError[c.sku] = true" />
          <span v-else>{{ c.sku }}</span>
        </div>
        <div class="ai-info">
          <div class="ai-name" :title="c.name" @click="ver(c)">{{ c.name }}</div>
          <div class="ai-meta">
            SKU {{ c.sku }} ·
            <span :class="c.stock > 0 ? 'ok' : 'out'">
              {{ c.stock > 0 ? `${c.stock} en existencia` : 'Agotado' }}
            </span>
          </div>
          <div class="ai-row">
            <span class="ai-price">${{ c.price.toFixed(2) }} {{ c.currency }}</span>
            <button class="ai-btn ghost" @click="ver(c)">Ver</button>
            <button class="ai-btn" :disabled="c.stock <= 0" @click="agregar(c)">
              {{ agregados[c.sku] ? '✓ Añadido' : c.stock <= 0 ? 'Agotado' : 'Añadir' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- DIBUJOS / VISTAS EXPLOSIONADAS -->
    <div v-if="reply.drawings?.length && !compact" class="ai-drawings">
      <a v-for="d in reply.drawings" :key="d.url" :href="ragUrl(d.url)" target="_blank" rel="noopener"
        :title="d.title">
        <img :src="ragUrl(d.url)" :alt="d.title" loading="lazy" />
      </a>
    </div>

    <!-- HOJAS TÉCNICAS Y FUENTES -->
    <div v-if="reply.downloads?.length || publicSources.length" class="ai-links">
      <a v-for="d in reply.downloads" :key="d.url" :href="ragUrl(d.url)" target="_blank" rel="noopener"
        class="ai-link dl">📄 {{ d.label || 'Hoja técnica' }}</a>
      <a v-for="s in publicSources" :key="s.url" :href="ragUrl(s.url)" target="_blank" rel="noopener"
        class="ai-link">🔗 {{ s.title }}</a>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ragUrl, type AssistantProduct, type AssistantReply } from '@/api/assistant'
import { renderAnswer } from '@/utils/assistantMarkdown'
import { useMarketStore, type ProductoExtendido } from '@/stores/market'
import { useCartStore } from '@/stores/cart'
import { useUiStore } from '@/stores/ui'
import type { Producto } from '@/api/inventory'

const props = defineProps<{ reply: AssistantReply; compact?: boolean }>()

const marketStore = useMarketStore()
const cartStore = useCartStore()
const uiStore = useUiStore()
const router = useRouter()
const imgError = reactive<Record<string, boolean>>({})
const agregados = reactive<Record<string, boolean>>({})

// misma llave que market.ts / Firebase
const llave = (v: unknown) =>
  String(v || '')
    .trim()
    .toLowerCase()
    .replace(/[.#$[\]]/g, '')
    .replaceAll('/', '')

const enTienda = (sku: string): Producto | undefined => {
  const k = llave(sku)
  return marketStore.productos.find((p) => {
    const x = p as ProductoExtendido
    return llave(x.id || x.ID || x.SKU || x['no. De parte'] || x.NO_DE_PARTE) === k
  })
}

// datos VIVOS de la tienda (stock / precio / foto) cuando el producto ya está cargado
const cards = computed<AssistantProduct[]>(() =>
  (props.reply.products || []).map((c) => {
    const vivo = enTienda(c.sku) as ProductoExtendido | undefined
    if (!vivo) return c
    const img = String(vivo.Imagen_URL || '')
    return {
      ...c,
      name: String(vivo.Producto || c.name),
      price: Number(vivo.Precio ?? c.price) || 0,
      stock: Number(vivo.Stock ?? c.stock) || 0,
      image: img && !img.startsWith('data:') ? img : c.image,
    }
  }),
)

const publicSources = computed(() =>
  (props.reply.sources || []).filter((s) => s.cited).slice(0, props.compact ? 2 : 5),
)

const html = computed(() =>
  renderAnswer(props.reply.answer, {
    sources: props.reply.sources || [],
    productName: (sku) => {
      const c = props.reply.products?.find((p) => llave(p.sku) === llave(sku))
      if (c) return c.name
      const vivo = enTienda(sku) as ProductoExtendido | undefined
      return vivo ? String(vivo.Producto || sku) : null
    },
  }),
)

const ver = async (c: AssistantProduct) => {
  const vivo = enTienda(c.sku)
  if (!vivo) {
    marketStore.searchQuery = c.sku // el catálogo aún no carga: lo busca por SKU
    uiStore.closeAll()
    router.push('/catalogo')
    return
  }
  uiStore.closeAll() // el panel lateral queda encima de la ficha técnica
  const conFicha = ['catalogo', 'perfil', 'asistente']
  if (!conFicha.includes(String(router.currentRoute.value.name))) {
    await router.push('/catalogo')
    await nextTick()
  }
  marketStore.openModal(vivo)
}

const agregar = (c: AssistantProduct) => {
  if (c.stock <= 0) return
  cartStore.agregarProducto(c.sku, c.stock, 1)
  agregados[c.sku] = true
}

const onTextClick = (e: MouseEvent) => {
  const el = (e.target as HTMLElement)?.closest?.('[data-sku]') as HTMLElement | null
  const sku = el?.dataset.sku
  if (!sku) return
  const c = cards.value.find((x) => llave(x.sku) === llave(sku))
  if (c) ver(c)
  else {
    const vivo = enTienda(sku)
    if (vivo) {
      uiStore.closeAll()
      marketStore.openModal(vivo)
    }
  }
}
</script>

<style scoped>
.ai-reply {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ai-text {
  font-size: 0.92rem;
  line-height: 1.6;
  word-wrap: break-word;
}
.ai-text :deep(p) {
  margin: 0 0 8px;
}
.ai-text :deep(p:last-child) {
  margin-bottom: 0;
}
.ai-text :deep(ul),
.ai-text :deep(ol) {
  margin: 4px 0 8px;
  padding-left: 20px;
}
.ai-text :deep(li) {
  margin-bottom: 4px;
}
.ai-text :deep(a) {
  color: var(--accent, #d32f2f);
}
.ai-text :deep(code) {
  background: var(--bg-input, #e9ecef);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 0.85em;
}
.ai-text :deep(.sku-chip) {
  display: inline-block;
  border: 1px solid var(--accent, #d32f2f);
  color: var(--accent, #d32f2f);
  background: transparent;
  border-radius: 999px;
  padding: 0 8px;
  margin: 0 2px;
  font-size: 0.78rem;
  font-weight: 700;
  line-height: 1.5;
  cursor: pointer;
}
.ai-text :deep(.sku-chip:hover) {
  background: var(--accent, #d32f2f);
  color: #fff;
}
.ai-text :deep(.src-ref) {
  text-decoration: none;
  font-weight: 700;
  font-size: 0.7rem;
  padding: 0 2px;
}

.ai-products {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ai-products-title {
  font-size: 0.7rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-muted, #6a737d);
}
.ai-product {
  display: flex;
  gap: 10px;
  padding: 8px;
  border: 1px solid var(--border, #d1d5da);
  border-radius: 12px;
  background: var(--bg-panel, #fff);
}
.ai-thumb {
  flex: 0 0 64px;
  height: 64px;
  border-radius: 8px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  cursor: pointer;
  font-size: 0.65rem;
  color: #6a737d;
  border: 1px solid var(--border, #e5e7eb);
}
.ai-thumb img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.ai-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ai-name {
  font-weight: 700;
  font-size: 0.85rem;
  line-height: 1.3;
  cursor: pointer;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
}
.ai-meta {
  font-size: 0.72rem;
  color: var(--text-muted, #6a737d);
}
.ai-meta .ok {
  color: #16a34a;
  font-weight: 700;
}
.ai-meta .out {
  color: #dc2626;
  font-weight: 700;
}
.ai-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}
.ai-price {
  font-weight: 800;
  font-size: 0.85rem;
  margin-right: auto;
}
.ai-btn {
  border: none;
  background: var(--accent, #d32f2f);
  color: #fff;
  font-weight: 700;
  font-size: 0.72rem;
  padding: 5px 10px;
  border-radius: 8px;
  cursor: pointer;
}
.ai-btn.ghost {
  background: transparent;
  color: var(--text-main, #1c1e21);
  border: 1px solid var(--border, #d1d5da);
}
.ai-btn:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
.ai-drawings {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.ai-drawings a {
  border: 1px solid var(--border, #d1d5da);
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}
.ai-drawings img {
  max-height: 120px;
  max-width: 180px;
  object-fit: contain;
  padding: 4px;
  display: block;
}
.ai-links {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.ai-link {
  font-size: 0.72rem;
  text-decoration: none;
  color: var(--text-main, #1c1e21);
  background: var(--bg-input, #e9ecef);
  border: 1px solid var(--border, #d1d5da);
  padding: 3px 9px;
  border-radius: 999px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-link.dl {
  border-color: var(--accent, #d32f2f);
  color: var(--accent, #d32f2f);
  font-weight: 700;
}
.compact .ai-text {
  font-size: 0.86rem;
}
</style>
