/** 界面主题系统：4 套预设风格，一键切换（顶栏下拉），localStorage 记住选择。
 *
 * 实现：CSS 变量写进 document.documentElement + Element Plus 暗色模式（html.dark）。
 * 图表颜色由 chartColors() 提供，BaseChart 统一应用，切换主题实时生效。
 */
import { ref } from 'vue'

export interface ThemeDef {
  name: string
  label: string
  elDark: boolean
  vars: Record<string, string>
}

export const THEMES: ThemeDef[] = [
  {
    name: 'business',
    label: '深蓝商务',
    elDark: false,
    vars: {
      '--app-bg': '#f5f7fa', '--app-card': '#ffffff', '--app-text': '#303133',
      '--app-text-secondary': '#909399', '--app-border': '#e4e7ed',
      '--sidebar-bg': '#1d2b3a', '--sidebar-text': '#c8d3e0', '--sidebar-active': '#409eff',
      '--header-bg': '#ffffff', '--el-color-primary': '#409eff',
    },
  },
  {
    name: 'light',
    label: '浅色简约',
    elDark: false,
    vars: {
      '--app-bg': '#fafbfc', '--app-card': '#ffffff', '--app-text': '#2c3e50',
      '--app-text-secondary': '#8c98a8', '--app-border': '#e8ebf0',
      '--sidebar-bg': '#f0f2f5', '--sidebar-text': '#4a5568', '--sidebar-active': '#3b82f6',
      '--header-bg': '#ffffff', '--el-color-primary': '#3b82f6',
    },
  },
  {
    name: 'dark',
    label: '暗色科技',
    elDark: true,
    vars: {
      '--app-bg': '#14161a', '--app-card': '#1d2127', '--app-text': '#d8dee8',
      '--app-text-secondary': '#7a8699', '--app-border': '#2c323c',
      '--sidebar-bg': '#0f1115', '--sidebar-text': '#aab6c6', '--sidebar-active': '#6d8dff',
      '--header-bg': '#1d2127', '--el-color-primary': '#6d8dff',
    },
  },
  {
    name: 'warm',
    label: '护眼米色',
    elDark: false,
    vars: {
      '--app-bg': '#f7f3e8', '--app-card': '#fdfaf2', '--app-text': '#4a4132',
      '--app-text-secondary': '#978b74', '--app-border': '#e6dcc4',
      '--sidebar-bg': '#3a3226', '--sidebar-text': '#d8cfbc', '--sidebar-active': '#d4a373',
      '--header-bg': '#fdfaf2', '--el-color-primary': '#b08968',
    },
  },
]

const STORAGE_KEY = 'weiwolianghua-theme'

export const currentTheme = ref<string>(
  localStorage.getItem(STORAGE_KEY) || 'business',
)

export function applyTheme(name: string) {
  const theme = THEMES.find((t) => t.name === name) || THEMES[0]
  const root = document.documentElement
  for (const [key, value] of Object.entries(theme.vars)) {
    root.style.setProperty(key, value)
  }
  root.classList.toggle('dark', theme.elDark)
  currentTheme.value = theme.name
  localStorage.setItem(STORAGE_KEY, theme.name)
}

export function initTheme() {
  applyTheme(currentTheme.value)
}

/** 当前主题的图表中性色（坐标轴/分割线/文字），BaseChart 统一应用。 */
export function chartColors() {
  const theme = THEMES.find((t) => t.name === currentTheme.value) || THEMES[0]
  return theme.elDark
    ? { axis: '#8a94a6', split: '#2c323c', text: '#aab6c6' }
    : { axis: '#909399', split: '#f0f2f5', text: '#606266' }
}
