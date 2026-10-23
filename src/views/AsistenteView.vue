<template>
  <div class="assistant-page">
    <NavBar @toggle-cart="uiStore.toggleCart()" />

    <header class="rag-header">
      <div class="rag-logo">R</div>
      <div class="header-info">
        <h1>RELANT · Asistente IA</h1>
        <div class="sub">
          Abnox · Schütze · Soma · Lubtec · Walther Systemtechnik — te guía al producto correcto
          con nuestras hojas técnicas, manuales y el catálogo de la tienda
        </div>
      </div>
      <div class="status">
        <span :class="['dot', assistant.online ? 'on' : assistant.online === false ? 'off' : '']"></span>
        <span>{{ assistant.statusText }}</span>
      </div>
      <button v-if="assistant.messages.length" class="btn-new" @click="assistant.nuevaConversacion()">
        Nueva conversación
      </button>
    </header>

    <main class="chat-container" ref="chatContainerRef">
      <div v-if="!assistant.messages.length" class="welcome-card">
        <h2>¿Qué necesitas? 👋</h2>
        <p>
          Descríbeme tu aplicación o el requerimiento —modelo de válvula, fluido, tipo y tamaño de
          rosca, manguera, presión— y te recomiendo los productos de la tienda que te sirven, con
          su precio y existencia. También puedo resolver dudas técnicas, mostrarte dibujos y
          generar la hoja técnica de un producto.
        </p>
        <div class="brands-row">
          <span class="brand-pill">🔩 Refacciones</span>
          <span class="brand-pill">💧 Equipos de dosificación</span>
          <span class="brand-pill">🛢️ Lubricación</span>
          <span class="brand-pill">⚙️ Conexiones hidráulicas y neumáticas</span>
        </div>
      </div>

      <template v-for="(m, idx) in assistant.messages" :key="idx">
        <div v-if="m.role === 'user'" class="msg user">
          <div class="bubble">{{ m.content }}</div>
        </div>
        <div v-else-if="m.role === 'assistant' && m.reply" class="msg bot">
          <div class="card-answer">
            <AssistantReply :reply="m.reply" />
            <div class="meta-row">
              <span class="pill">⏱ {{ m.reply.seconds }} s</span>
              <span v-if="m.reply.products.length" class="pill">
                🛒 {{ m.reply.products.length }} producto(s)
              </span>
              <span v-if="m.reply.sources.length" class="pill">
                📚 {{ m.reply.sources.length }} documento(s)
              </span>
            </div>
          </div>
        </div>
        <div v-else class="msg bot">
          <div class="bubble error">⚠️ {{ m.content }}</div>
        </div>
      </template>

      <div v-if="assistant.sending" class="msg bot">
        <div class="bubble loading-bubble">
          <div class="spinner"></div>
          Buscando en el catálogo y en la documentación técnica…
        </div>
      </div>
    </main>

    <footer class="input-footer">
      <div class="foot-inner">
        <div class="chips">
          <span v-for="(chip, i) in sugerencias" :key="i" class="chip" @click="preguntar(chip)">
            {{ chip }}
          </span>
        </div>
        <div class="input-row">
          <textarea
            v-model="preguntaInput"
            rows="1"
            maxlength="1500"
            placeholder="Escribe tu pregunta… (Enter para enviar, Shift+Enter para nueva línea)"
            @keydown.enter.exact.prevent="preguntar()"
          ></textarea>
          <button class="btn-send" :disabled="assistant.sending || !preguntaInput.trim()"
            @click="preguntar()">
            Preguntar
          </button>
        </div>
        <div class="hint">
          Las recomendaciones usan el stock y precio actuales de la tienda · verifica la
          compatibilidad con tu equipo antes de comprar
        </div>
      </div>
    </footer>

    <ProductModal />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useUiStore } from '@/stores/ui'
import { useAssistantStore } from '@/stores/assistant'
import NavBar from '@/components/NavBar.vue'
import ProductModal from '@/components/ProductModal.vue'
import AssistantReply from '@/components/AssistantReply.vue'

const uiStore = useUiStore()
const assistant = useAssistantStore()
const preguntaInput = ref('')
const chatContainerRef = ref<HTMLElement | null>(null)
let timer: number | null = null

const sugerencias = [
  'Necesito una aguja para un cordón fino en válvula VDV-25',
  '¿Qué conector neumático uso para manguera de 6 mm y rosca 1/4 NPT?',
  'Busco conexiones hidráulicas de alta presión 3/8',
  '¿Qué refacciones tiene el producto que estoy viendo?',
  '¿Cómo le doy mantenimiento a una válvula dosificadora?',
]

const scrollAlFondo = async () => {
  await nextTick()
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' })
}

const preguntar = async (texto?: string) => {
  const q = (texto ?? preguntaInput.value).trim()
  if (!q || assistant.sending) return
  preguntaInput.value = ''
  const p = assistant.enviar(q)
  await scrollAlFondo()
  await p
  await scrollAlFondo()
}

watch(() => assistant.messages.length, scrollAlFondo)

onMounted(() => {
  assistant.verificarServidor()
  timer = window.setInterval(() => assistant.verificarServidor(), 60000)
  scrollAlFondo()
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.assistant-page {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  background-color: var(--bg-main, #f3f6fa);
  color: var(--text-main, #1b2733);
}
.rag-header {
  background: linear-gradient(135deg, #0f2b46, #1e4976);
  color: #ffffff;
  padding: 14px 24px;
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  box-shadow: 0 2px 10px rgba(15, 43, 70, 0.25);
}
.rag-logo {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: var(--accent, #e52e2e);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 20px;
}
.header-info {
  flex: 1;
  min-width: 220px;
}
.header-info h1 {
  font-size: 1.05rem;
  font-weight: 800;
  margin: 0;
}
.header-info .sub {
  font-size: 0.78rem;
  opacity: 0.85;
}
.status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.78rem;
  background: rgba(255, 255, 255, 0.12);
  padding: 6px 14px;
  border-radius: 999px;
}
.dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #94a3b8;
}
.dot.on {
  background: #4ade80;
  box-shadow: 0 0 8px #4ade80;
}
.dot.off {
  background: #f87171;
}
.btn-new {
  background: transparent;
  color: #fff;
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 0.78rem;
  cursor: pointer;
}
.btn-new:hover {
  background: rgba(255, 255, 255, 0.12);
}
.chat-container {
  flex: 1;
  padding: 24px 16px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  max-width: 900px;
  width: 100%;
  margin: 0 auto;
  box-sizing: border-box;
}
.welcome-card {
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #e3e8ef);
  border-radius: 16px;
  padding: 26px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.04);
}
.welcome-card h2 {
  font-size: 1.2rem;
  margin: 0 0 8px;
}
.welcome-card p {
  color: var(--text-muted, #64748b);
  font-size: 0.92rem;
  line-height: 1.55;
  margin: 0;
}
.brands-row {
  margin-top: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.brand-pill {
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--accent, #e52e2e);
  background: var(--bg-input, #f1f5f9);
  border: 1px solid var(--border, #e2e8f0);
  padding: 5px 12px;
  border-radius: 999px;
}
.msg {
  display: flex;
  flex-direction: column;
  max-width: 90%;
}
.msg.user {
  align-self: flex-end;
}
.msg.bot {
  align-self: flex-start;
  width: 100%;
  max-width: 100%;
}
.msg.user .bubble {
  background: var(--accent, #e52e2e);
  color: #ffffff;
  padding: 12px 18px;
  border-radius: 16px;
  border-bottom-right-radius: 4px;
  font-weight: 600;
  font-size: 0.93rem;
  white-space: pre-wrap;
}
.card-answer {
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #e3e8ef);
  border-radius: 16px;
  overflow: hidden;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.04);
}
.card-answer > :first-child {
  padding: 16px 20px;
}
.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 20px;
  background: var(--bg-input, #f8fafc);
  border-top: 1px solid var(--border, #e3e8f0);
  font-size: 0.72rem;
}
.pill {
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #e2e8f0);
  padding: 2px 10px;
  border-radius: 999px;
  font-weight: 700;
}
.loading-bubble {
  background: var(--bg-panel, #ffffff);
  border: 1px solid var(--border, #e2e8f0);
  color: var(--text-muted, #64748b);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 18px;
  border-radius: 16px;
  width: fit-content;
}
.spinner {
  width: 18px;
  height: 18px;
  border: 3px solid var(--border, #e2e8f0);
  border-top-color: var(--accent, #e52e2e);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.bubble.error {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
  padding: 12px 18px;
  border-radius: 12px;
  width: fit-content;
}
.input-footer {
  position: sticky;
  bottom: 0;
  background: var(--bg-panel, #ffffff);
  border-top: 1px solid var(--border, #e3e8f0);
  padding: 14px 16px;
}
.foot-inner {
  max-width: 900px;
  margin: 0 auto;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.chip {
  font-size: 0.76rem;
  color: var(--text-main, #0f172a);
  background: var(--bg-input, #f1f5f9);
  border: 1px solid var(--border, #cbd5e1);
  padding: 5px 12px;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.2s ease;
}
.chip:hover {
  background: var(--accent, #e52e2e);
  color: #ffffff;
  border-color: var(--accent, #e52e2e);
}
.input-row {
  display: flex;
  gap: 10px;
}
textarea {
  flex: 1;
  resize: none;
  border: 1px solid var(--border, #cbd5e1);
  border-radius: 12px;
  padding: 12px 16px;
  font-size: 0.95rem;
  font-family: inherit;
  background: var(--bg-input, #f8fafc);
  color: var(--text-main, #0f172a);
  outline: none;
}
textarea:focus {
  border-color: var(--accent, #e52e2e);
}
.btn-send {
  background: var(--accent, #e52e2e);
  color: #ffffff;
  border: none;
  border-radius: 12px;
  padding: 0 24px;
  font-weight: 800;
  cursor: pointer;
}
.btn-send:hover:not(:disabled) {
  background: var(--accent-hover, #c22525);
}
.btn-send:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
.hint {
  font-size: 0.72rem;
  color: var(--text-muted, #64748b);
  margin-top: 8px;
  text-align: center;
}
@media (max-width: 600px) {
  .chips {
    display: none;
  }
  .foot-inner {
    padding-right: 62px; /* deja libre el botón flotante de tema */
  }
  .btn-send {
    padding: 0 14px;
  }
}
</style>
