import { createRouter, createWebHashHistory } from 'vue-router'

import HomeView from '../views/HomeView.vue'
import KnowledgeView from '../views/KnowledgeView.vue'
import ObjectView from '../views/ObjectView.vue'
import ClaimView from '../views/ClaimView.vue'
import QaView from '../views/QaView.vue'
import ResearchView from '../views/ResearchView.vue'
import ReviewView from '../views/ReviewView.vue'
import CorrectionView from '../views/CorrectionView.vue'
import EvalDashboard from '../views/EvalDashboard.vue'
import AgentView from '../views/AgentView.vue'
import SettingsView from '../views/SettingsView.vue'
import DatabaseView from '../views/DatabaseView.vue'

const routes = [
  { path: '/', component: HomeView },
  { path: '/knowledge', component: KnowledgeView },
  { path: '/knowledge/object/:id', component: ObjectView },
  { path: '/knowledge/claim/:id', component: ClaimView },
  { path: '/qa', component: QaView },
  { path: '/research', component: ResearchView },
  { path: '/review', component: ReviewView },
  { path: '/correction', component: CorrectionView },
  { path: '/agent', component: AgentView },
  { path: '/settings', component: SettingsView },
  { path: '/settings/database', component: DatabaseView },
  { path: '/eval', component: EvalDashboard },
]

export const router = createRouter({ history: createWebHashHistory(), routes })
