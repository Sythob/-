<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AssistantChat from './components/AssistantChat.vue'
import { applyTheme, currentTheme, THEMES } from './themes'

const route = useRoute()

const menus = [
  { path: '/dashboard', title: '总览' },
  { path: '/market', title: '行情' },
  { path: '/market-data', title: '市场数据' },
  { path: '/backtest', title: '回测' },
  { path: '/strategy', title: '策略' },
  { path: '/paper', title: '模拟交易' },
  { path: '/ai', title: 'AI 报告' },
  { path: '/system', title: '系统' },
]

const currentTitle = computed(() => (route.meta.title as string) || '唯我量化')
const currentThemeLabel = computed(
  () => THEMES.find((t) => t.name === currentTheme.value)?.label || '主题',
)

function switchTheme(name: string) {
  applyTheme(name)
}
</script>

<template>
  <el-container class="layout">
    <el-aside width="180px" class="sidebar">
      <div class="logo">唯我量化</div>
      <el-menu
        :default-active="route.path"
        router
        background-color="var(--sidebar-bg)"
        text-color="var(--sidebar-text)"
        active-text-color="var(--sidebar-active)"
      >
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          {{ m.title }}
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="header-title">{{ currentTitle }}</span>
        <el-dropdown class="theme-switch" trigger="click" @command="switchTheme">
          <el-button size="small" link>
            🎨 {{ currentThemeLabel }} <span style="margin-left: 2px">▼</span>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item
                v-for="t in THEMES"
                :key="t.name"
                :command="t.name"
                :class="{ 'theme-active': t.name === currentTheme }"
              >
                {{ t.label }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
  <AssistantChat />
</template>

<style scoped>
.layout {
  height: 100%;
}

.sidebar {
  background-color: var(--sidebar-bg);
}

.logo {
  height: 60px;
  line-height: 60px;
  text-align: center;
  color: var(--sidebar-text);
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 2px;
}

.sidebar :deep(.el-menu) {
  border-right: none;
}

.header {
  background: var(--header-bg);
  border-bottom: 1px solid var(--app-border);
  display: flex;
  align-items: center;
}

.header-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--app-text);
}

.theme-switch {
  margin-left: auto;
  margin-right: 8px;
}

:deep(.theme-active) {
  color: var(--el-color-primary);
  font-weight: 600;
}

.main {
  padding: 16px;
}
</style>
