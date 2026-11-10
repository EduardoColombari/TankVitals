import type { Component } from 'vue'
import {
  Bot,
  Box,
  BrainCircuit,
  Cpu,
  HeartPulse,
  LayoutDashboard,
  ScrollText,
  TestTubes,
  Workflow,
  Wrench,
} from 'lucide-vue-next'
import type { Permission } from '../stores/auth'

export type MenuGroup = 'Operação' | 'Inteligência artificial' | 'Sistema'

export const MENU_GROUPS: MenuGroup[] = ['Operação', 'Inteligência artificial', 'Sistema']

export interface PageDef {
  path: string
  name: string
  title: string
  icon: Component
  group: MenuGroup
  /**
   * A tela de verdade. Sem ela, a rota mostra o placeholder com a tarefa que
   * vai construí-la. Quem fizer a tela troca aqui, e mais nada.
   */
  component?: () => Promise<Component>
  /** Permissão para abrir a tela; sem ela, qualquer pessoa logada abre. */
  permission?: Permission
  /** Tarefa do backlog (docs/TAREFAS-2BIM.md) que constrói a tela. */
  task?: string
  description?: string
  /** Ação principal da tela, mostrada no placeholder só para quem pode fazê-la. */
  action?: { label: string; permission: Permission }
}

export const PAGES: PageDef[] = [
  {
    path: 'dashboard',
    name: 'dashboard',
    title: 'Dashboard',
    icon: LayoutDashboard,
    group: 'Operação',
    // O dashboard puxa o Chart.js; carregar só quando a rota abre deixa o login leve.
    component: () => import('../views/DashboardView.vue'),
  },
  {
    path: 'gemeo-3d',
    name: 'gemeo-3d',
    title: 'Gêmeo 3D',
    icon: Box,
    group: 'Operação',
    // O Three.js é pesado: só carrega quando a rota abre.
    component: () => import('../views/TwinView.vue'),
    task: 'WEB-05 a WEB-08',
    description: 'O tanque em 3D, movido só pelos dados que chegam ao vivo pelo WebSocket.',
  },
  {
    path: 'dispositivos',
    name: 'dispositivos',
    title: 'Dispositivos',
    icon: Cpu,
    group: 'Operação',
    component: () => import('../views/DevicesView.vue'),
    task: 'WEB-03',
    description:
      'Cadastro e gerenciamento dos sensores, dos atuadores e da bomba, com as faixas normais e as regras.',
    action: { label: 'Cadastrar dispositivo', permission: 'editRegistry' },
  },
  {
    path: 'laboratorio',
    component: () => import('../views/LabView.vue'),
    name: 'laboratorio',
    title: 'Laboratório',
    icon: TestTubes,
    group: 'Inteligência artificial',
    task: 'IA-03',
    description: 'Geração de dados sintéticos em massa, com os parâmetros e o progresso do job.',
    action: { label: 'Gerar dados', permission: 'generateData' },
  },
  {
    path: 'treinamento',
    component: () => import('../views/TrainingView.vue'),
    name: 'treinamento',
    title: 'Treinamento',
    icon: BrainCircuit,
    group: 'Inteligência artificial',
    task: 'IA-06',
    description:
      'Treino dos modelos de regressão, com as métricas comparadas à base simples e a versão em uso.',
    action: { label: 'Treinar modelos', permission: 'train' },
  },
  {
    path: 'insights',
    name: 'insights',
    title: 'Insights',
    icon: HeartPulse,
    group: 'Inteligência artificial',
    component: () => import('../views/InsightsView.vue'),
    task: 'IA-09',
    description: 'Saúde, vida útil restante, tendência e alertas de cada equipamento.',
  },
  {
    path: 'assistente',
    component: () => import('../views/AssistantView.vue'),
    name: 'assistente',
    title: 'Assistente',
    icon: Bot,
    group: 'Inteligência artificial',
    task: 'BACK-10',
    description: 'Chat com o LLM local, que responde sobre os dados do sistema.',
  },
  {
    path: 'mensageria',
    name: 'mensageria',
    title: 'Mensageria',
    icon: Workflow,
    group: 'Sistema',
    component: () => import('../views/MessagingView.vue'),
    task: 'PLAT-07',
    description: 'O RabbitMQ ao vivo: filas, taxas, consumidores, últimas mensagens e a DLQ.',
  },
  {
    path: 'auditoria',
    name: 'auditoria',
    title: 'Auditoria',
    icon: ScrollText,
    group: 'Sistema',
    permission: 'viewAudit',
    component: () => import('../views/AuditView.vue'),
    task: 'WEB-04',
    description: 'Logins, comandos e jobs registrados no PostgreSQL.',
  },
  {
    path: 'ferramentas',
    name: 'ferramentas',
    title: 'Ferramentas',
    icon: Wrench,
    group: 'Sistema',
    component: () => import('../views/ToolsView.vue'),
    task: 'WEB-04',
    description: 'Links das interfaces do InfluxDB, do RabbitMQ, do pgAdmin e da documentação da API.',
  },
]
