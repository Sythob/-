import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', name: 'dashboard', component: () => import('../views/DashboardView.vue'), meta: { title: '总览' } },
    { path: '/market', name: 'market', component: () => import('../views/MarketView.vue'), meta: { title: '行情' } },
    { path: '/market-data', name: 'market-data', component: () => import('../views/MarketDataView.vue'), meta: { title: '市场数据' } },
    { path: '/backtest', name: 'backtest', component: () => import('../views/BacktestView.vue'), meta: { title: '回测' } },
    { path: '/strategy', name: 'strategy', component: () => import('../views/StrategyView.vue'), meta: { title: '策略' } },
    { path: '/paper', name: 'paper', component: () => import('../views/PaperView.vue'), meta: { title: '模拟交易' } },
    { path: '/ai', name: 'ai', component: () => import('../views/AiReportView.vue'), meta: { title: 'AI 报告' } },
    { path: '/system', name: 'system', component: () => import('../views/SystemView.vue'), meta: { title: '系统' } },
  ],
})

export default router
