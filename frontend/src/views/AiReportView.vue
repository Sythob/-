<script setup lang="ts">
import { marked } from 'marked'
import { computed, onMounted, ref } from 'vue'

import request from '../api/request'

interface ReportItem {
  id: number
  report_type: string
  title: string
  model: string
  created_at: string
}

const reports = ref<ReportItem[]>([])
const activeType = ref('daily')
const activeId = ref<number | null>(null)
const content = ref('')
const loading = ref(false)

const TYPE_NAMES: Record<string, string> = {
  daily: '复盘日报', weekly: '周报', news_forecast: '热点预测', news: '新闻情绪', stock: '个股解读', multiagent: '多智能体', codegen: '代码生成',
}

async function loadList() {
  try {
    reports.value = (await request.get('/ai/reports', {
      params: { type: activeType.value, limit: 50 },
    })) as ReportItem[]
    if (reports.value.length && activeId.value === null) {
      await loadDetail(reports.value[0].id)
    }
  } catch { reports.value = [] }
}

// ---------------- 预测战绩台账
const predStats = ref<Record<string, any> | null>(null)
const predRows = ref<Record<string, any>[]>([])

const VERDICT_TAG: Record<string, { label: string; type: string }> = {
  correct: { label: '✅ 对', type: 'success' },
  wrong: { label: '❌ 错', type: 'danger' },
  partial: { label: '⚠️ 部分对', type: 'warning' },
  pending: { label: '⏳ 待核验', type: 'info' },
}

async function loadPredictions() {
  try {
    const data = (await request.get('/ai/predictions', { params: { limit: 200 } })) as {
      stats: Record<string, any>
      rows: Record<string, any>[]
    }
    predStats.value = data.stats
    predRows.value = data.rows
  } catch {
    predStats.value = null
    predRows.value = []
  }
}

async function generateWeekly() {
  loading.value = true
  try {
    await request.post('/ai/weekly/generate')
    activeType.value = 'weekly'
    activeId.value = null
    await loadList()
  } finally {
    loading.value = false
  }
}

// ---------------- 多智能体分析（页签内发起）
const maSymbol = ref('')
const maRunning = ref(false)
const maStatus = ref('')
let maTimer: number | undefined

async function runMultiagent() {
  if (!maSymbol.value.trim()) return
  maRunning.value = true
  maStatus.value = '分析中：四分析师并行 → 研究经理 → 交易员 → 风控官（约 1~2 分钟，7 次真实模型调用）...'
  try {
    const resp = (await request.post('/ai/multiagent', { symbol: maSymbol.value.trim() })) as { task_id: string }
    maTimer = window.setInterval(async () => {
      try {
        const task = (await request.get(`/system/tasks/${resp.task_id}`)) as Record<string, any>
        if (task.status === 'done') {
          window.clearInterval(maTimer)
          maRunning.value = false
          maStatus.value = '分析完成'
          activeType.value = 'multiagent'
          activeId.value = null
          await loadList()
        } else if (task.status === 'failed') {
          window.clearInterval(maTimer)
          maRunning.value = false
          maStatus.value = `分析失败：${task.error || '未知错误'}`
        }
      } catch { /* 轮询失败重试 */ }
    }, 5000)
  } catch {
    maRunning.value = false
    maStatus.value = '任务提交失败'
  }
}

// ---------------- 新闻情绪（展示已打分新闻，数据在 News 表而非报告表）
const newsSentiment = ref<{ date: string; stats: Record<string, number>; rows: Record<string, any>[] } | null>(null)

const SENTIMENT_TAG: Record<string, { label: string; type: string; color: string }> = {
  利好: { label: '利好', type: 'danger', color: '#e05a4e' },
  利空: { label: '利空', type: 'success', color: '#3c9e63' },
  中性: { label: '中性', type: 'info', color: '#909399' },
}

async function loadNewsSentiment() {
  try {
    newsSentiment.value = (await request.get('/ai/news-sentiment', { params: { limit: 200 } })) as never
  } catch { newsSentiment.value = null }
}

async function loadDetail(id: number) {
  activeId.value = id
  loading.value = true
  try {
    const detail = (await request.get(`/ai/reports/${id}`)) as { content_md: string }
    content.value = detail.content_md
  } finally {
    loading.value = false
  }
}

function switchType(type: string) {
  activeType.value = type
  activeId.value = null
  if (type === 'predictions') {
    loadPredictions()
  } else if (type === 'news') {
    loadNewsSentiment()
  } else {
    loadList()
  }
}

async function generateReport() {
  loading.value = true
  try {
    await request.post('/ai/reports/generate')
    await loadList()
  } finally {
    loading.value = false
  }
}

async function runSentiment() {
  loading.value = true
  try {
    await request.post('/ai/sentiment', null, { params: { limit: 30 } })
  } finally {
    loading.value = false
  }
}

const forecastRunning = ref(false)
const forecastStatus = ref('')
let forecastTimer: number | undefined

async function runNewsForecast() {
  forecastRunning.value = true
  forecastStatus.value = '生成中：抓取今日热点新闻 → 大模型分析 → 明日行情预测（约 1 分钟）...'
  try {
    const resp = (await request.post('/ai/news_forecast')) as { task_id: string }
    forecastTimer = window.setInterval(async () => {
      try {
        const task = (await request.get(`/system/tasks/${resp.task_id}`)) as Record<string, any>
        if (task.status === 'done') {
          window.clearInterval(forecastTimer)
          forecastRunning.value = false
          forecastStatus.value = '预测已生成'
          activeType.value = 'news_forecast'
          activeId.value = null
          await loadList()
        } else if (task.status === 'failed') {
          window.clearInterval(forecastTimer)
          forecastRunning.value = false
          forecastStatus.value = `生成失败：${task.error || '未知错误'}`
        }
      } catch { /* 轮询失败重试 */ }
    }, 5000)
  } catch {
    forecastRunning.value = false
    forecastStatus.value = '任务提交失败'
  }
}

const html = computed(() => {
  if (!content.value) return ''
  return marked.parse(content.value, { async: false }) as string
})

onMounted(loadList)
</script>

<template>
  <el-row :gutter="12">
    <el-col :span="8">
      <el-card shadow="never">
        <template #header>
          AI 报告
          <span class="header-actions">
            <el-button size="small" type="primary" @click="generateReport">生成日报</el-button>
            <el-button size="small" type="success" @click="generateWeekly">生成周报</el-button>
            <el-button size="small" type="warning" :loading="forecastRunning" @click="runNewsForecast">热点预测</el-button>
          </span>
        </template>
        <el-tabs v-model="activeType" @tab-change="switchType">
          <el-tab-pane v-for="(name, type) in TYPE_NAMES" :key="type" :label="name" :name="type" />
          <el-tab-pane label="预测战绩" name="predictions" />
        </el-tabs>
        <div v-if="forecastStatus" class="tip" style="margin-bottom: 8px">{{ forecastStatus }}</div>
        <template v-if="activeType !== 'predictions' && activeType !== 'news'">
          <div v-if="activeType === 'multiagent'" style="margin-bottom: 10px">
            <el-input v-model="maSymbol" placeholder="输入代码或名称，如 600519 或 茅台" size="small" style="width: 220px" @keyup.enter="runMultiagent" />
            <el-button size="small" type="primary" :loading="maRunning" style="margin-left: 8px" @click="runMultiagent">
              发起多智能体分析
            </el-button>
            <div v-if="maStatus" class="tip" style="margin-top: 6px">{{ maStatus }}</div>
          </div>
          <el-table :data="reports" size="small" max-height="470" highlight-current-row @row-click="(row: ReportItem) => loadDetail(row.id)">
            <el-table-column prop="title" label="标题" />
            <el-table-column prop="model" label="模型" width="70" />
            <el-table-column prop="created_at" label="时间" width="150" />
          </el-table>
          <el-empty v-if="!reports.length" description="暂无报告" :image-size="60" />
        </template>
        <template v-else-if="activeType === 'news'">
          <div style="margin-bottom: 10px">
            <el-button size="small" @click="runSentiment">新闻情绪打分</el-button>
            <span class="tip" style="margin-left: 8px">每日 17:30 自动抓取打分</span>
          </div>
          <div v-if="newsSentiment" style="margin-bottom: 10px">
            <span class="tip">{{ newsSentiment.date }} 起已打分新闻：</span>
            <el-tag v-for="(n, label) in newsSentiment.stats" :key="label" size="small"
              :type="(SENTIMENT_TAG[label] || {}).type" style="margin-right: 8px">
              {{ label }} {{ n }} 条
            </el-tag>
          </div>
          <el-table :data="newsSentiment?.rows || []" size="small" max-height="450">
            <el-table-column prop="pub_time" label="时间" width="140" />
            <el-table-column label="股票" width="140">
              <template #default="{ row }">{{ row.name || '--' }}<span class="tip">（{{ row.symbol || '大盘' }}）</span></template>
            </el-table-column>
            <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
            <el-table-column label="情绪" width="80">
              <template #default="{ row }">
                <el-tag :type="(SENTIMENT_TAG[row.label] || {}).type" size="small">{{ row.label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="理由" min-width="140" show-overflow-tooltip />
          </el-table>
          <el-empty v-if="!newsSentiment?.rows?.length" description="暂无已打分新闻（点击右上角「新闻情绪打分」或等收盘后自动打分）" :image-size="60" />
          <div class="tip">数据源：公开财经新闻 → LLM 逐条打分（利好/中性/利空 + 理由）</div>
        </template>
        <template v-else>
          <el-descriptions v-if="predStats" :column="3" size="small" border style="margin-bottom: 10px">
            <el-descriptions-item label="总数">{{ predStats.total }}</el-descriptions-item>
            <el-descriptions-item label="对">{{ predStats.correct }}</el-descriptions-item>
            <el-descriptions-item label="错">{{ predStats.wrong }}</el-descriptions-item>
            <el-descriptions-item label="部分对">{{ predStats.partial }}</el-descriptions-item>
            <el-descriptions-item label="待核验">{{ predStats.pending }}</el-descriptions-item>
            <el-descriptions-item label="准确率">
              <b v-if="predStats.accuracy !== null" :style="{ color: predStats.accuracy >= 50 ? '#e05a4e' : '#3c9e63' }">
                {{ predStats.accuracy }}%
              </b>
              <span v-else>--</span>
            </el-descriptions-item>
          </el-descriptions>
          <el-table :data="predRows" size="small" max-height="430">
            <el-table-column prop="report_date" label="日期" width="90" />
            <el-table-column label="类型" width="70">
              <template #default="{ row }">{{ row.horizon === 'weekly' ? '周' : '日' }}/{{ row.category }}</template>
            </el-table-column>
            <el-table-column prop="content" label="预测内容" min-width="180" show-overflow-tooltip />
            <el-table-column label="结果" width="95">
              <template #default="{ row }">
                <el-tag :type="(VERDICT_TAG[row.verdict] || {}).type" size="small">
                  {{ (VERDICT_TAG[row.verdict] || {}).label || row.verdict }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="actual" label="实际情况" min-width="140" show-overflow-tooltip />
          </el-table>
          <el-empty v-if="!predRows.length" description="暂无预测记录（生成日报/周报后自动提取）" :image-size="60" />
          <div class="tip">预测从每期报告的「明日关注/下周预测」自动提取，次日自动核验对错；错误的教训自动沉淀进下次报告</div>
        </template>
      </el-card>
    </el-col>
    <el-col :span="16">
      <el-card shadow="never" v-loading="loading">
        <template #header>报告详情</template>
        <div v-if="html" class="markdown-body" v-html="html" />
        <el-empty v-else description="选择左侧报告查看" />
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
.markdown-body {
  line-height: 1.8;
  font-size: 14px;
  max-height: 620px;
  overflow-y: auto;
}

.markdown-body :deep(h1) {
  font-size: 20px;
  border-bottom: 1px solid var(--app-border);
  padding-bottom: 8px;
}

.markdown-body :deep(h2) {
  font-size: 16px;
  margin-top: 16px;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 22px;
}
.tip {
  color: var(--app-text-secondary);
  font-size: 12px;
}
</style>
