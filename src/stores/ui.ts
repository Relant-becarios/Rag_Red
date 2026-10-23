import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useUiStore = defineStore('ui', () => {
  const isCartOpen = ref(false)
  const isMenuOpen = ref(false)
  const isAuthModalOpen = ref(false)
  const isChatOpen = ref(false)

  // LÓGICA DE TEMAS PERSISTENTE (Busca 'theme' o 'tema')
  const temaGuardado = localStorage.getItem('theme') || localStorage.getItem('tema') || 'light'
  const temaActual = ref(temaGuardado)

  const aplicarTemaDOM = (tema: string) => {
    document.documentElement.setAttribute('data-theme', tema)
    if (document.body) {
      document.body.setAttribute('data-theme', tema)
    }

    if (tema === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
  }

  // Aplica el tema guardado de inmediato al cargar la tienda
  aplicarTemaDOM(temaActual.value)

  function toggleTema() {
    temaActual.value = temaActual.value === 'dark' ? 'light' : 'dark'
    aplicarTemaDOM(temaActual.value)

    // Guarda en ambos nombres de clave para prevenir cualquier desincronización
    localStorage.setItem('theme', temaActual.value)
    localStorage.setItem('tema', temaActual.value)
  }

  const isDarkTheme = computed(() => temaActual.value === 'dark')

  function toggleCart() {
    isCartOpen.value = !isCartOpen.value
    if (isCartOpen.value) {
      isMenuOpen.value = false
      isAuthModalOpen.value = false
      isChatOpen.value = false
    }
  }

  function toggleMenu() {
    isMenuOpen.value = !isMenuOpen.value
    if (isMenuOpen.value) {
      isCartOpen.value = false
      isAuthModalOpen.value = false
      isChatOpen.value = false
    }
  }

  function toggleAuthModal() {
    isAuthModalOpen.value = !isAuthModalOpen.value
    if (isAuthModalOpen.value) {
      isCartOpen.value = false
      isMenuOpen.value = false
      isChatOpen.value = false
    }
  }

  function toggleChat() {
    isChatOpen.value = !isChatOpen.value
    if (isChatOpen.value) {
      isCartOpen.value = false
      isMenuOpen.value = false
      isAuthModalOpen.value = false
    }
  }

  function closeAll() {
    isCartOpen.value = false
    isMenuOpen.value = false
    isAuthModalOpen.value = false
    isChatOpen.value = false
  }

  return {
    isCartOpen,
    isMenuOpen,
    isAuthModalOpen,
    isChatOpen,
    temaActual,
    isDarkTheme,
    toggleCart,
    toggleMenu,
    toggleAuthModal,
    toggleChat,
    toggleTema,
    closeAll,
  }
})
