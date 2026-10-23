<template>
  <div :class="['chat-panel', { open: uiStore.isChatOpen }]" role="dialog" aria-label="Asistente IA">
    <div class="chat-header">
      <div>
        <h3>✨ Asistente Relant</h3>
        <div class="chat-sub">
          <span :class="['dot', assistant.online === false ? 'off' : assistant.online ? 'on' : '']"></span>
          Te ayuda a elegir el producto correcto
        </div>
      </div>
      <div class="header-actions">
        <button class="icon-btn" title="Abrir en pantalla completa" @click="abrirCompleto">⤢</button>
        <button class="icon-btn" title="Nueva conversación" @click="assistant.nuevaConversacion()">↺</button>
        <button class="icon-btn" title="Cerrar" @click="uiStore.closeAll">✕</button>
      </div>
    </div>

    <div class="chat-messages" ref="messagesContainer">
      <div v-if="!assistant.messages.length" class="msg msg-bot">
        ¡Hola! Cuéntame qué necesitas —equipo, válvula, tipo de rosca, manguera, fluido— y te
        recomiendo los productos de la tienda que te sirven.
        <div class="chips">
          <button v-for="s in sugerencias" :key="s" class="chip" @click="enviar(s)">{{ s }}</button>
        </div>
      </div>

      <template v-for="(m, i) in assistant.messages" :key="i">
        <div v-if="m.role === 'user'" class="msg msg-user">{{ m.content }}</div>
        <div v-else-if="m.role === 'assistant' && m.reply" class="msg msg-bot wide">
          <AssistantReply :reply="m.reply" compact />
        </div>
        <div v-else class="msg msg-error">⚠️ {{ m.content }}</div>
      </template>

      <div v-if="assistant.sending" class="msg msg-bot typing">
        <span></span><span></span><span></span>
      </div>
    </div>

    <div class="chat-input-area">
      <div class="input-wrapper">
        <input
          type="text"
          v-model="newMessage"
          maxlength="1500"
          placeholder="Ej. conector neumático 1/4 NPT para manguera de 6 mm"
          @keydown.enter.prevent="enviar()"
        />
        <button class="send-btn" :disabled="assistant.sending || !newMessage.trim()" @click="enviar()">➤</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import { useAssistantStore } from '@/stores/assistant'
import AssistantReply from '@/components/AssistantReply.vue'

const uiStore = useUiStore()
const assistant = useAssistantStore()
const router = useRouter()
const messagesContainer = ref<HTMLElement | null>(null)
const newMessage = ref('')

const sugerencias = [
  '¿Qué aguja uso para un cordón fino?',
  'Conector neumático para manguera de 6 mm',
  '¿Qué refacciones tiene el producto que estoy viendo?',
]

const scrollBottom = async () => {
  await nextTick()
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

const enviar = async (texto?: string) => {
  const q = (texto ?? newMessage.value).trim()
  if (!q) return
  newMessage.value = ''
  const p = assistant.enviar(q)
  await scrollBottom()
  await p
  await scrollBottom()
}

const abrirCompleto = () => {
  uiStore.closeAll()
  router.push('/asistente')
}

watch(
  () => uiStore.isChatOpen,
  (open) => {
    if (open) {
      if (assistant.online === null) assistant.verificarServidor()
      scrollBottom()
    }
  },
)
</script>

<style scoped>
.chat-panel {
  position: fixed;
  top: 0;
  right: -440px;
  width: 420px;
  max-width: 100vw;
  height: 100vh;
  height: 100dvh;
  background: var(--bg-panel, #fff);
  color: var(--text-main, #1c1e21);
  z-index: 25000;
  transition: right 0.35s ease;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--border, #30363d);
  box-shadow: -8px 0 24px rgba(0, 0, 0, 0.12);
}
.chat-panel.open {
  right: 0;
}
@media (max-width: 480px) {
  .chat-panel {
    width: 100vw;
    right: -100vw;
  }
}
.chat-header {
  padding: 14px 16px;
  background: var(--bg-input, #e9ecef);
  border-bottom: 1px solid var(--border, #30363d);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.chat-header h3 {
  margin: 0;
  color: var(--accent, #ff0000);
  font-size: 1rem;
}
.chat-sub {
  font-size: 0.72rem;
  color: var(--text-muted, #6a737d);
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #94a3b8;
}
.dot.on {
  background: #22c55e;
}
.dot.off {
  background: #ef4444;
}
.header-actions {
  display: flex;
  gap: 4px;
}
.icon-btn {
  background: transparent;
  border: none;
  color: var(--text-main, #1c1e21);
  font-size: 17px;
  cursor: pointer;
  width: 30px;
  height: 30px;
  border-radius: 8px;
}
.icon-btn:hover {
  background: var(--border, #d1d5da);
}
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.msg {
  max-width: 88%;
  padding: 10px 13px;
  border-radius: 14px;
  font-size: 0.88rem;
  line-height: 1.45;
}
.msg-user {
  align-self: flex-end;
  background: var(--accent, #ff0000);
  color: #fff;
  border-bottom-right-radius: 4px;
  white-space: pre-wrap;
}
.msg-bot {
  align-self: flex-start;
  background: var(--bg-input, #e9ecef);
  border-bottom-left-radius: 4px;
}
.msg-bot.wide {
  max-width: 100%;
  width: 100%;
}
.msg-error {
  align-self: flex-start;
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.chip {
  font-size: 0.75rem;
  background: var(--bg-panel, #fff);
  color: var(--text-main, #1c1e21);
  border: 1px solid var(--border, #d1d5da);
  border-radius: 999px;
  padding: 5px 10px;
  cursor: pointer;
  text-align: left;
}
.chip:hover {
  border-color: var(--accent, #ff0000);
  color: var(--accent, #ff0000);
}
.typing {
  display: flex;
  gap: 4px;
  padding: 14px;
}
.typing span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-muted, #6a737d);
  animation: blink 1.2s infinite ease-in-out;
}
.typing span:nth-child(2) {
  animation-delay: 0.2s;
}
.typing span:nth-child(3) {
  animation-delay: 0.4s;
}
@keyframes blink {
  0%,
  80%,
  100% {
    opacity: 0.25;
  }
  40% {
    opacity: 1;
  }
}
.chat-input-area {
  padding: 12px;
  border-top: 1px solid var(--border, #30363d);
}
.input-wrapper {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-input, #0f1215);
  padding: 6px 6px 6px 14px;
  border-radius: 25px;
  border: 1px solid var(--border, #30363d);
}
.input-wrapper input {
  flex: 1;
  min-width: 0;
  background: transparent;
  border: none;
  color: var(--text-main, #1c1e21);
  outline: none;
  font-size: 0.88rem;
}
.send-btn {
  border: none;
  background: var(--accent, #ff0000);
  color: #fff;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  cursor: pointer;
  font-size: 15px;
}
.send-btn:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
</style>
