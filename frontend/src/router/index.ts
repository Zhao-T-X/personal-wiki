import { createRouter, createWebHashHistory } from 'vue-router'

import HomeView from '../views/HomeView.vue'

/* 首页保持同步导入（首屏不闪），其余视图按需加载：
   把 Reka UI、cytoscape 等重依赖摊到各个 chunk，首屏包显著变小。 */
const routes = [
  { path: '/', component: HomeView },
  { path: '/knowledge', component: () => import('../views/KnowledgeView.vue') },
  { path: '/knowledge/object/:id', component: () => import('../views/ObjectView.vue') },
  { path: '/knowledge/claim/:id', component: () => import('../views/ClaimView.vue') },
  { path: '/qa', component: () => import('../views/QaView.vue') },
  { path: '/research', component: () => import('../views/ResearchView.vue') },
  { path: '/review', component: () => import('../views/ReviewView.vue') },
  { path: '/correction', component: () => import('../views/CorrectionView.vue') },
  { path: '/agent', component: () => import('../views/AgentView.vue') },
  { path: '/settings', component: () => import('../views/SettingsView.vue') },
  { path: '/settings/database', component: () => import('../views/DatabaseView.vue') },
  { path: '/eval', component: () => import('../views/EvalDashboard.vue') },
  { path: '/extraction-experiment', component: () => import('../views/ExtractionExperimentView.vue') },
]

export const router = createRouter({ history: createWebHashHistory(), routes })
