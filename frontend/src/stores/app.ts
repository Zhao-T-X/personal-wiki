import { defineStore } from 'pinia'
import { api } from '../api/client'
import type { Health } from '../api/types'

export const useAppStore = defineStore('app', {
  state: () => ({
    health: null as Health | null,
    toastMsg: '',
    searchTerm: '',
  }),
  actions: {
    async loadHealth() {
      try { this.health = await api<Health>('/api/health') } catch { this.health = null }
    },
    toast(msg: string) {
      this.toastMsg = msg
      window.setTimeout(() => (this.toastMsg = ''), 1800)
    },
  },
})
