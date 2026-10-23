<template>
  <div class="industrial-calc-section">
    <h2 class="calc-title">CALCULADORA DE DOSIFICACIÓN</h2>

    <div class="calc-grid">
      <div class="input-card">
        <label class="input-label">TAMAÑO GOTA</label>
        <input type="number" v-model.number="gota" class="calc-input" />
      </div>

      <div class="input-card">
        <label class="input-label">GRAMAJE</label>
        <input type="number" v-model.number="gramaje" class="calc-input" />
      </div>

      <div class="input-card">
        <label class="input-label">TIEMPO CICLO (S)</label>
        <input type="number" v-model.number="tiempo" class="calc-input" />
      </div>
    </div>

    <h1 class="result-title">
      RESULTADO: <span class="result-value">{{ resultado.toFixed(4) }}</span>
    </h1>

    <!-- FLECHA DE REGRESO -->
    <button class="btn-back-floating" @click="goBack" title="Volver">
      <img src="/Flecha.png" alt="Volver" />
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const gota = ref<number>(0)
const gramaje = ref<number>(0)
const tiempo = ref<number>(0)

const resultado = computed(() => {
  if (tiempo.value !== 0) {
    return (gota.value * gramaje.value) / tiempo.value
  }
  return 0
})

const goBack = () => {
  router.back()
}
</script>

<style scoped>
.industrial-calc-section {
  background: var(--bg-panel);
  color: var(--text-main);
  padding: 50px 5%;
  text-align: center;
  border-top: 2px solid var(--accent);
  min-height: calc(100vh - 60px);
  display: flex;
  flex-direction: column;
  justify-content: center;
  position: relative;
}
.calc-title {
  letter-spacing: 2px;
  color: var(--accent);
  margin-bottom: 40px;
}
.calc-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 20px;
  max-width: 1000px;
  margin: 0 auto;
  width: 100%;
}
.input-card {
  background: var(--bg-input);
  padding: 20px;
  border-radius: 12px;
  border: 1px solid var(--border);
}
.input-label {
  display: block;
  color: var(--accent);
  font-size: 12px;
  font-weight: bold;
  margin-bottom: 10px;
}
.calc-input {
  width: 100%;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--border);
  color: var(--text-main);
  text-align: center;
  font-size: 24px;
  outline: none;
  padding: 5px;
}
.calc-input::-webkit-outer-spin-button,
.calc-input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
.result-title {
  font-size: 40px;
  margin-top: 50px;
}
.result-value {
  color: var(--accent);
}

/* BOTÓN FLOTANTE EN ESQUINA INFERIOR IZQUIERDA */
.btn-back-floating {
  position: fixed;
  bottom: 25px;
  left: 25px;
  background: transparent;
  border: none;
  cursor: pointer;
  z-index: 20000;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  transition:
    transform 0.2s ease,
    opacity 0.2s ease;
}

.btn-back-floating:hover {
  transform: scale(1.15);
  opacity: 0.85;
}

.btn-back-floating img {
  width: 45px;
  height: 45px;
  object-fit: contain;
}
</style>
