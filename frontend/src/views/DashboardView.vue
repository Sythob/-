<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { marked } from 'marked'
import { computed, onMounted, ref } from 'vue'

import request from '../api/request'
import BaseChart from '../components/BaseChart.vue'

interface IndexQuote {
  symbol: string
  name?: string
  last_price: number
  pct_change?: number
  change?: number
  trade_date?: string
}

function sparklineOption(symbol: string): EChartsOption {
  const history = indexHistories.value[symbol] || []
  const values = history.map((h) => h.close)
  const up = values.length >= 2 && values[values.length - 1] >= values[0]
  return {
    grid: { left: 0, right: 0, top: 2, bottom: 2 },
    xAxis: { type: 'category', show: false, data: history.map((h) => h.date) },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{
      type: 'line', data: values, smooth: true, symbol: 'none',
      lineStyle: { width: 1.5, color: up ? '#e03131' : '#2f9e44' },
      areaStyle: { color: up ? 'rgba(224,49,49,0.08)' : 'rgba(47,158,68,0.08)' },
    }],
    tooltip: { show: false },
    animation: false,
  }
}

interface IndexGroup {
  key: string
  name: string
  indices: IndexQuote[]
  unavailable?: string[]
  error?: string
}

const indexGroups = ref<IndexGroup[]>([])
const indexHistories = ref<Record<string, { date: string; close: number }[]>>({})
const activeGroup = ref('a')
const quotes = ref<Record<string, any>[]>([])
const account = ref<Record<string, any> | null>(null)
const sentiment = ref<{ breadth: any[]; margin: Record<string, any>; note?: string } | null>(null)
const latestReport = ref<{ id: number; title: string; content_md: string; created_at: string } | null>(null)
const equity = ref<{ date: string; equity: number }[]>([])
const quotesError = ref('')

async function loadAll() {
  try {
    // 指数多源聚合冷启动较慢，单独放宽超时
    const data = (await request.get('/market/indices', { timeout: 90000 })) as {
      groups: IndexGroup[]
      histories: Record<string, { date: string; close: number }[]>
    }
    indexGroups.value = data.groups || []
    indexHistories.value = data.histories || {}
    if (indexGroups.value.length && !indexGroups.value.some((g) => g.key === activeGroup.value)) {
      activeGroup.value = indexGroups.value[0].key
    }
  } catch { /* 指数获取失败（离线）忽略 */ }
  try {
    quotes.value = (await request.get('/market/quotes')) as Record<string, any>[]
  } catch (e: any) {
    quotesError.value = '实时行情暂不可用（本地数据仍可回测）'
  }
  try {
    account.value = (await request.get('/paper/account')) as Record<string, any>
  } catch { /* 未初始化模拟盘 */ }
  try {
    sentiment.value = (await request.get('/market/sentiment', { timeout: 60000 })) as typeof sentiment.value
  } catch { /* 情绪数据暂不可用 */ }
  try {
    const reports = (await request.get('/ai/reports', { params: { type: 'daily', limit: 1 } })) as any[]
    if (reports.length) {
      const detail = (await request.get(`/ai/reports/${reports[0].id}`)) as any
      latestReport.value = detail
    }
  } catch { /* 暂无报告 */ }
  try {
    equity.value = (await request.get('/paper/equity')) as { date: string; equity: number }[]
  } catch { /* 暂无净值 */ }
}

/** 进入系统自动校验今日数据：缺失则后台补齐，完成后自动刷新页面数据。 */
async function ensureFresh() {
  try {
    const resp = (await request.post('/market/ensure_fresh')) as { task_id?: string; status?: string }
    if (!resp.task_id) return  // 非交易日或无需处理
    const timer = window.setInterval(async () => {
      try {
        const task = (await request.get(`/system/tasks/${resp.task_id}`)) as Record<string, any>
        if (task.status === 'done' || task.status === 'failed') {
          window.clearInterval(timer)
          await loadAll()
        }
      } catch { /* 轮询失败忽略 */ }
    }, 5000)
  } catch { /* 校验失败不影响页面 */ }
}

onMounted(() => {
  loadAll()
  ensureFresh()
})

const reportHtml = computed(() => {
  if (!latestReport.value) return ''
  return marked.parse(latestReport.value.content_md, { async: false }) as string
})

const equityOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 64, right: 20, top: 20, bottom: 30 },
  xAxis: { type: 'category', data: equity.value.map((e) => e.date), axisLabel: { color: '#909399' } },
  yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { color: '#f0f2f5' } }, axisLabel: { color: '#909399' } },
  series: [{
    name: '模拟盘净值', type: 'line', data: equity.value.map((e) => e.equity),
    smooth: true, symbol: 'none', lineStyle: { width: 2, color: '#2f6fed' },
    areaStyle: { color: 'rgba(47,111,237,0.08)' },
  }],
}))

function fmtPct(v: number | undefined): string {
  return v === undefined ? '--' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}
</script>

<template>
  <el-card shadow="never" class="index-card">
    <el-tabs v-model="activeGroup">
      <el-tab-pane v-for="g in indexGroups" :key="g.key" :label="g.name" :name="g.key">
        <el-row :gutter="12">
          <el-col v-for="idx in g.indices" :key="idx.symbol" :span="g.indices.length <= 3 ? 8 : 4">
            <div class="stat-box">
              <div class="stat-title">{{ idx.name }}</div>
              <div class="stat-value" :class="(idx.pct_change || 0) >= 0 ? 'text-up' : 'text-down'">
                {{ idx.last_price?.toFixed(2) }}
              </div>
              <div class="stat-sub" :class="(idx.pct_change || 0) >= 0 ? 'text-up' : 'text-down'">
                <span v-if="idx.change !== undefined">{{ idx.change > 0 ? '+' : '' }}{{ idx.change?.toFixed(2) }}</span>
                {{ fmtPct(idx.pct_change) }}
                <span v-if="idx.trade_date" class="stat-date">（{{ idx.trade_date }}）</span>
              </div>
              <BaseChart
                v-if="indexHistories[idx.symbol]?.length"
                :option="sparklineOption(idx.symbol)"
                height="34px"
                class="stat-spark"
              />
            </div>
          </el-col>
        </el-row>
        <el-alert v-if="g.error" :title="`${g.name}指数获取失败：${g.error}`" type="warning" :closable="false" />
        <p v-if="g.unavailable?.length" class="stat-note">暂不可用：{{ g.unavailable.join('、') }}（数据源已下线）</p>
      </el-tab-pane>
    </el-tabs>
  </el-card>

  <el-row :gutter="12" style="margin-top: 12px">
    <el-col :span="8">
      <el-card shadow="never">
        <template #header>模拟账户</template>
        <template v-if="account">
          <el-descriptions :column="2" size="small">
            <el-descriptions-item label="总资产">{{ account.total?.toLocaleString() }}</el-descriptions-item>
            <el-descriptions-item label="可用现金">{{ account.cash?.toLocaleString() }}</el-descriptions-item>
            <el-descriptions-item label="持仓市值">{{ account.market_value?.toLocaleString() }}</el-descriptions-item>
            <el-descriptions-item label="累计盈亏">
              <span :class="account.total_pnl >= 0 ? 'text-up' : 'text-down'">
                {{ account.total_pnl?.toLocaleString() }}
              </span>
            </el-descriptions-item>
          </el-descriptions>
        </template>
        <el-empty v-else description="模拟账户未初始化" :image-size="60" />
      </el-card>
    </el-col>
    <el-col :span="8">
      <el-card shadow="never">
        <template #header>市场情绪</template>
        <div v-if="sentiment?.breadth?.length" class="sentiment-row">
          <div class="sentiment-item text-up">涨停 {{ sentiment.breadth[0].limit_up }}</div>
          <div class="sentiment-item text-down">跌停 {{ sentiment.breadth[0].limit_down }}</div>
          <div class="sentiment-item text-up">上涨 {{ sentiment.breadth[0].advance }}</div>
          <div class="sentiment-item text-down">下跌 {{ sentiment.breadth[0].decline }}</div>
        </div>
        <div v-if="sentiment?.breadth?.length" class="sentiment-date">{{ sentiment.breadth[0].date }}</div>
        <div v-else class="sentiment-date">广度数据将在收盘后自动同步（或点系统页「同步数据」立即获取）</div>
        <div v-if="sentiment?.margin?.margin_balance" class="sentiment-margin">
          两融余额：{{ (sentiment.margin.margin_balance / 1e8).toFixed(0) }} 亿元（{{ sentiment.margin.source }}）
        </div>
        <div v-if="sentiment?.note" class="sentiment-note">{{ sentiment.note }}</div>
      </el-card>
    </el-col>
    <el-col :span="8">
      <el-card shadow="never">
        <template #header>自选池行情</template>
        <el-table v-if="quotes.length" :data="quotes" size="small" max-height="200">
          <el-table-column prop="symbol" label="代码" width="80" />
          <el-table-column prop="name" label="名称" />
          <el-table-column label="最新价" width="100">
            <template #default="{ row }">{{ row.last_price?.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="涨跌幅" width="100">
            <template #default="{ row }">
              <span :class="(row.pct_change || 0) >= 0 ? 'text-up' : 'text-down'">{{ fmtPct(row.pct_change) }}</span>
            </template>
          </el-table-column>
        </el-table>
        <el-alert v-else-if="quotesError" :title="quotesError" type="info" :closable="false" />
        <el-empty v-else description="暂无数据" :image-size="60" />
      </el-card>
    </el-col>
  </el-row>

  <el-row :gutter="12" style="margin-top: 12px">
    <el-col :span="12">
      <el-card shadow="never">
        <template #header>模拟盘净值曲线</template>
        <BaseChart v-if="equity.length" :option="equityOption" height="260px" />
        <el-empty v-else description="暂无净值数据（结算后生成）" :image-size="60" />
      </el-card>
    </el-col>
    <el-col :span="12">
      <el-card shadow="never">
        <template #header>
          最新 AI 复盘日报
          <el-button link type="primary" @click="$router.push('/ai')">更多 →</el-button>
        </template>
        <div v-if="latestReport" class="report-preview" v-html="reportHtml" />
        <el-empty v-else description="暂无日报（配置 DeepSeek Key 或 LLM_MOCK=1 后可用）" :image-size="60" />
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
.index-card {
  margin-bottom: 12px;
}

.stat-box {
  text-align: center;
  background: #f8f9fb;
  border-radius: 6px;
  padding: 10px 0;
  margin-bottom: 8px;
}

.stat-title {
  color: var(--app-text-secondary);
  font-size: 13px;
}

.stat-value {
  font-size: 22px;
  font-weight: 600;
  margin: 6px 0;
}

.stat-sub {
  font-size: 13px;
}

.stat-date {
  color: var(--app-text-secondary);
  font-size: 12px;
}

.stat-spark {
  margin-top: 6px;
}

.stat-note {
  color: var(--app-text-secondary);
  font-size: 12px;
  margin-top: 4px;
}

.sentiment-row {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.sentiment-item {
  font-size: 15px;
  font-weight: 600;
}

.sentiment-date,
.sentiment-margin,
.sentiment-note {
  color: var(--app-text-secondary);
  font-size: 12px;
  margin-top: 6px;
}

.report-preview {
  max-height: 260px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.7;
}

.report-preview :deep(h1) {
  font-size: 16px;
}

.report-preview :deep(h2) {
  font-size: 14px;
  margin-top: 8px;
}
</style>
