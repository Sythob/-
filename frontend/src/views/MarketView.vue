<script setup lang="ts">
import { marked } from 'marked'
import { computed, onMounted, ref } from 'vue'

import request from '../api/request'
import KlineChart, { type KBar } from '../components/KlineChart.vue'
import MinuteChart, { type MinutePoint } from '../components/MinuteChart.vue'
import { resampleBars } from '../utils/bars'

const watchlist = ref<string[]>([])
const quotes = ref<Record<string, any>[]>([])
const searchResults = ref<Record<string, any>[]>([])
const sectors = ref<Record<string, any>[]>([])
const tscreenCandidates = ref<Record<string, any>[]>([])
const tscreenWatch = ref<Record<string, any>[]>([])
const tscreenWatchDate = ref('')
const marketState = ref<Record<string, any> | null>(null)
const boardCandidates = ref<Record<string, any>[]>([])
const boardRefreshing = ref(false)
const boardStatus = ref('')
let boardTimer: number | undefined
const keyword = ref('')
const activeSymbol = ref('')
const adjust = ref<'raw' | 'qfq' | 'hfq'>('qfq')
const interval = ref<'minute' | 'daily' | '60m'>('daily')
const period = ref<'day' | 'week' | 'month' | 'quarter' | 'year'>('day')
const bars = ref<KBar[]>([])
const minutePoints = ref<MinutePoint[]>([])
const loading = ref(false)
const quotesError = ref('')
const analyzeDialog = ref(false)
const analyzeLoading = ref(false)
const analyzeContent = ref('')

async function runAnalyze() {
  if (!activeSymbol.value) return
  analyzeDialog.value = true
  analyzeLoading.value = true
  analyzeContent.value = ''
  try {
    const resp = (await request.post('/ai/analyze', { symbol: activeSymbol.value })) as {
      content_md: string
    }
    analyzeContent.value = resp.content_md
  } finally {
    analyzeLoading.value = false
  }
}

const multiagentDialog = ref(false)
const multiagentLoading = ref(false)
const multiagentContent = ref('')
let multiagentTimer: number | undefined

async function runMultiagent() {
  if (!activeSymbol.value) return
  multiagentDialog.value = true
  multiagentLoading.value = true
  multiagentContent.value = '分析中：四位分析师并行研判 → 经理汇总 → 交易员决策 → 风控审查（约 1~2 分钟）...'
  try {
    const resp = (await request.post('/ai/multiagent', { symbol: activeSymbol.value })) as { task_id: string }
    multiagentTimer = window.setInterval(async () => {
      try {
        const task = (await request.get(`/system/tasks/${resp.task_id}`)) as Record<string, any>
        if (task.status === 'done') {
          window.clearInterval(multiagentTimer)
          multiagentLoading.value = false
          multiagentContent.value = task.result.content_md
        } else if (task.status === 'failed') {
          window.clearInterval(multiagentTimer)
          multiagentLoading.value = false
          multiagentContent.value = `分析失败：${task.error || '未知错误'}`
        }
      } catch { /* 轮询失败重试 */ }
    }, 3000)
  } catch {
    multiagentLoading.value = false
    multiagentContent.value = '任务提交失败，请检查后端服务'
  }
}

const multiagentHtml = computed(() =>
  multiagentContent.value ? (marked.parse(multiagentContent.value, { async: false }) as string) : '',
)

const analyzeHtml = computed(() =>
  analyzeContent.value ? (marked.parse(analyzeContent.value, { async: false }) as string) : '',
)

async function loadWatchlist() {
  try {
    const cfg = (await request.get('/system/config')) as { watchlist: string[] }
    watchlist.value = cfg.watchlist || []
  } catch { watchlist.value = [] }
}

async function loadQuotes() {
  try {
    // 行情源冷启动较慢（新浪全市场快照），放宽超时；缓存命中时秒回
    const data = (await request.get('/market/quotes', { timeout: 120000 })) as Record<string, any>[]
    quotes.value = data
    quotesError.value = ''
  } catch (e: any) {
    quotesError.value = e?.response?.data?.detail || '实时行情获取失败'
  }
}

async function loadBars(symbol: string) {
  activeSymbol.value = symbol
  loading.value = true
  try {
    if (interval.value === 'minute') {
      minutePoints.value = (await request.get(`/market/minutes/${symbol}`, {
        timeout: 120000,
      })) as MinutePoint[]
      // 分时也需要日线历史（K线切换时用），一并加载
      bars.value = (await request.get(`/market/bars/${symbol}`, {
        params: { adjust: adjust.value, interval: 'daily' },
      })) as KBar[]
    } else {
      bars.value = (await request.get(`/market/bars/${symbol}`, {
        params: { adjust: adjust.value, interval: interval.value },
      })) as KBar[]
    }
  } catch {
    bars.value = []
    minutePoints.value = []
  } finally {
    loading.value = false
  }
}

function onIntervalChange() {
  if (activeSymbol.value) loadBars(activeSymbol.value)
}

const displayBars = computed(() => {
  if (interval.value === '60m' || period.value === 'day') return bars.value
  return resampleBars(bars.value, period.value as 'week' | 'month' | 'quarter' | 'year')
})

// 当前标的的实时行情（行情信息条用）
const activeQuote = computed(() =>
  quotes.value.find((q) => q.symbol === activeSymbol.value) || null,
)

async function loadSectors() {
  try {
    sectors.value = (await request.get('/market/sectors', { timeout: 60000 })) as Record<string, any>[]
  } catch { /* 板块数据暂不可用 */ }
}

async function loadTscreen() {
  try {
    const data = (await request.get('/market/tscreen', { params: { limit: 20 } })) as {
      candidates: Record<string, any>[]
      watch_zone: Record<string, any>[]
      market_state: Record<string, any>
    }
    tscreenCandidates.value = data.candidates || []
    tscreenWatch.value = data.watch_zone || []
    tscreenWatchDate.value = tscreenWatch.value[0]?.date || ''
    marketState.value = data.market_state || null
  } catch {
    tscreenCandidates.value = []
    tscreenWatch.value = []
    marketState.value = null
  }
}

async function loadBoardScreen() {
  try {
    boardCandidates.value = (await request.get('/market/boardscreen')) as Record<string, any>[]
  } catch { boardCandidates.value = [] }
}

/** 打板数据日期与过期判断（周末/节假日不告警） */
const boardDate = computed(() => boardCandidates.value[0]?.date || '')
const boardStale = computed(() => {
  if (!boardDate.value) return false
  const today = new Date()
  const day = today.getDay()
  if (day === 0 || day === 6) return false
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  return boardDate.value < todayStr
})

async function refreshBoardScreen() {
  boardRefreshing.value = true
  boardStatus.value = '打板池刷新中：全市场涨停扫描 → 连板/换手标注（约 2~4 分钟）...'
  try {
    const resp = (await request.post('/market/boardscreen/refresh')) as { task_id: string }
    boardTimer = window.setInterval(async () => {
      try {
        const task = (await request.get(`/system/tasks/${resp.task_id}`)) as Record<string, any>
        if (task.status === 'done') {
          window.clearInterval(boardTimer)
          boardRefreshing.value = false
          boardStatus.value = `完成：打板候选 ${task.result.coarse} 只`
          await loadBoardScreen()
        } else if (task.status === 'failed') {
          window.clearInterval(boardTimer)
          boardRefreshing.value = false
          boardStatus.value = `刷新失败：${task.error || '未知错误'}`
        }
      } catch { /* 轮询失败重试 */ }
    }, 5000)
  } catch {
    boardRefreshing.value = false
    boardStatus.value = '任务提交失败'
  }
}

async function addCandidatesToWatch() {
  const symbols = [...watchlist.value]
  for (const c of tscreenCandidates.value) {
    if (!symbols.includes(c.symbol)) symbols.push(c.symbol)
  }
  try {
    const resp = (await request.put('/system/watchlist', { symbols })) as { watchlist: string[] }
    watchlist.value = resp.watchlist
  } catch { /* 保存失败（拦截器已提示） */ }
}

const tscreenRefreshing = ref(false)
const tscreenStatus = ref('')
let tscreenTimer: number | undefined

async function refreshTscreen() {
  tscreenRefreshing.value = true
  tscreenStatus.value = '全市场扫描中：快照粗筛 5000+ 只 → 定向同步候选日线 → 精筛（约 3~6 分钟）...'
  try {
    const resp = (await request.post('/market/tscreen/refresh')) as { task_id: string }
    tscreenTimer = window.setInterval(async () => {
      try {
        const task = (await request.get(`/system/tasks/${resp.task_id}`)) as Record<string, any>
        if (task.status === 'done') {
          window.clearInterval(tscreenTimer)
          tscreenRefreshing.value = false
          const r = task.result
          tscreenStatus.value = `完成：全市场粗筛 ${r.coarse} 只 → 定向同步 → 精选 ${r.candidates.length} 只`
          await loadTscreen()
        } else if (task.status === 'failed') {
          window.clearInterval(tscreenTimer)
          tscreenRefreshing.value = false
          tscreenStatus.value = `刷新失败：${task.error || '未知错误'}`
        }
      } catch { /* 轮询失败重试 */ }
    }, 5000)
  } catch {
    tscreenRefreshing.value = false
    tscreenStatus.value = '任务提交失败，请检查后端服务'
  }
}

async function search() {
  if (!keyword.value.trim()) return
  try {
    searchResults.value = (await request.get('/market/instruments', {
      params: { keyword: keyword.value.trim() },
    })) as Record<string, any>[]
  } catch { searchResults.value = [] }
}

async function toggleWatch(symbol: string) {
  const next = watchlist.value.includes(symbol)
    ? watchlist.value.filter((s) => s !== symbol)
    : [...watchlist.value, symbol]
  try {
    const resp = (await request.put('/system/watchlist', { symbols: next })) as { watchlist: string[] }
    watchlist.value = resp.watchlist
  } catch { /* 保存失败：保持原状态（拦截器已提示） */ }
}

onMounted(async () => {
  await loadWatchlist()
  await loadQuotes()
  loadSectors()
  loadTscreen()
  loadBoardScreen()
  if (watchlist.value.length) await loadBars(watchlist.value[0])
})
</script>

<template>
  <el-row :gutter="12">
    <el-col :span="8">
      <el-card shadow="never">
        <template #header>
          自选池
          <span class="header-actions">
            <el-button link type="primary" @click="loadQuotes">刷新行情</el-button>
          </span>
        </template>
        <el-alert v-if="quotesError" :title="quotesError" type="warning" :closable="false" style="margin-bottom: 8px" />
        <el-table
          :data="quotes" size="small" highlight-current-row
          @row-click="(row: any) => loadBars(row.symbol)"
        >
          <el-table-column prop="symbol" label="代码" width="80" />
          <el-table-column prop="name" label="名称" />
          <el-table-column label="最新" width="90">
            <template #default="{ row }">{{ row.last_price?.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="涨跌" width="90">
            <template #default="{ row }">
              <span :class="(row.pct_change || 0) >= 0 ? 'text-up' : 'text-down'">
                {{ row.pct_change ? `${row.pct_change > 0 ? '+' : ''}${row.pct_change.toFixed(2)}%` : '--' }}
              </span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!quotes.length && !quotesError" description="暂无行情" :image-size="60" />

        <el-divider />
        <div class="search-row">
          <el-input v-model="keyword" placeholder="搜索股票代码/名称" size="small" @keyup.enter="search" />
          <el-button size="small" type="primary" @click="search">搜索</el-button>
        </div>
        <el-table :data="searchResults" size="small" max-height="180" style="margin-top: 8px"
                  highlight-current-row @row-click="(row: any) => loadBars(row.symbol)">
          <el-table-column prop="symbol" label="代码" width="80" />
          <el-table-column prop="name" label="名称" />
          <el-table-column prop="board" label="板块" width="80" />
          <el-table-column label="操作" width="70">
            <template #default="{ row }">
              <el-button link size="small" @click.stop="toggleWatch(row.symbol)">
                {{ watchlist.includes(row.symbol) ? '移出自选' : '加自选' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <el-divider />
        <div class="section-title">
          行业板块（新浪）
          <el-button link type="primary" size="small" @click="loadSectors">刷新</el-button>
        </div>
        <el-table :data="sectors.slice(0, 10)" size="small" max-height="220">
          <el-table-column prop="name" label="板块" />
          <el-table-column label="涨跌幅" width="80">
            <template #default="{ row }">
              <span :class="(row.pct_change || 0) >= 0 ? 'text-up' : 'text-down'">
                {{ row.pct_change ? `${row.pct_change > 0 ? '+' : ''}${row.pct_change.toFixed(2)}%` : '--' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="leader" label="领涨股" width="80" />
        </el-table>

        <el-divider />
        <div class="section-title">
          做T候选池（本地库筛选）
          <el-button link type="primary" size="small" @click="loadTscreen">刷新</el-button>
          <el-button link type="success" size="small" :loading="tscreenRefreshing" @click="refreshTscreen">
            全市场扫描
          </el-button>
        </div>
        <div v-if="tscreenStatus" class="tip">{{ tscreenStatus }}</div>
        <el-alert
          v-if="marketState"
          :type="marketState.regime === 'strong' ? 'error' : marketState.regime === 'weak' ? 'success' : 'info'"
          :closable="false" style="margin-bottom: 8px"
        >
          <template #title>
            {{ marketState.regime === 'strong' ? '市场状态：强势' : marketState.regime === 'weak' ? '市场状态：弱势' : marketState.regime === 'neutral' ? '市场状态：震荡' : '市场状态：未知' }}
            <span class="tip">（{{ marketState.date }}，上涨 {{ marketState.advance }} / 下跌 {{ marketState.decline }}，涨停 {{ marketState.limit_up }} / 跌停 {{ marketState.limit_down }}）</span>
          </template>
          {{ marketState.advice }}
        </el-alert>
        <div class="section-subtitle">
          精选候选（振幅/换手/趋势全达标{{ marketState?.trend_strict ? '，弱势已收紧至 MA20+MA60 双向上' : '' }}）
          <el-button v-if="tscreenCandidates.length" link type="primary" size="small" @click="addCandidatesToWatch">
            一键加入自选
          </el-button>
        </div>
        <el-table :data="tscreenCandidates" size="small" max-height="160" highlight-current-row
                  @row-click="(row: any) => loadBars(row.symbol)">
          <el-table-column prop="name" label="名称" width="90" />
          <el-table-column label="振幅%" width="70">
            <template #default="{ row }">{{ row.amplitude_avg?.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="成交额" width="80">
            <template #default="{ row }">{{ (row.amount_avg / 1e8).toFixed(1) }}亿</template>
          </el-table-column>
          <el-table-column label="20日" width="70">
            <template #default="{ row }">
              <span :class="(row.ret_20 || 0) >= 0 ? 'text-up' : 'text-down'">
                {{ row.ret_20 > 0 ? '+' : '' }}{{ row.ret_20?.toFixed(1) }}%
              </span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!tscreenCandidates.length" description="暂无精选（点全市场扫描）" :image-size="50" />

        <div class="section-subtitle">
          观察区（今日振幅/成交额合格，未过趋势或换手精筛{{ tscreenWatchDate ? ' · ' + tscreenWatchDate : '' }}）
        </div>
        <el-table :data="tscreenWatch" size="small" max-height="280" highlight-current-row
                  @row-click="(row: any) => loadBars(row.symbol)">
          <el-table-column prop="name" label="名称" width="90" />
          <el-table-column label="今日振幅" width="80">
            <template #default="{ row }">{{ row.amplitude_today?.toFixed(2) }}%</template>
          </el-table-column>
          <el-table-column label="今日成交" width="80">
            <template #default="{ row }">{{ (row.amount_today / 1e8).toFixed(1) }}亿</template>
          </el-table-column>
          <el-table-column label="涨跌" width="70">
            <template #default="{ row }">
              <span :class="(row.pct_change || 0) >= 0 ? 'text-up' : 'text-down'">
                {{ row.pct_change > 0 ? '+' : '' }}{{ row.pct_change?.toFixed(1) }}%
              </span>
            </template>
          </el-table-column>
        </el-table>
        <p class="tip">筛选标准：精选需成交额≥5亿、振幅≥3%、换手3~10%、20日线向上；观察区为粗筛（今日振幅≥3%、成交额≥3亿、非ST非涨跌停）。点击行查看 K 线</p>
        <p class="tip">提示：自选池修改保存后，在系统页点击「同步数据」生效</p>
      </el-card>
    </el-col>
    <el-col :span="16">
      <el-card shadow="never" v-loading="loading">
        <template #header>
          <span v-if="activeSymbol">{{ activeSymbol }} {{ interval === 'minute' ? '分时' : 'K线' }}</span>
          <span v-else>选择标的查看行情</span>
          <el-radio-group v-model="interval" size="small" style="margin-left: 12px" @change="onIntervalChange">
            <el-radio-button value="minute">分时</el-radio-button>
            <el-radio-button value="60m">60分钟</el-radio-button>
            <el-radio-button value="daily">日K</el-radio-button>
          </el-radio-group>
          <el-radio-group v-if="interval === 'daily'" v-model="period" size="small" style="margin-left: 12px">
            <el-radio-button value="day">日</el-radio-button>
            <el-radio-button value="week">周</el-radio-button>
            <el-radio-button value="month">月</el-radio-button>
            <el-radio-button value="quarter">季</el-radio-button>
            <el-radio-button value="year">年</el-radio-button>
          </el-radio-group>
          <el-radio-group v-if="interval === 'daily'" v-model="adjust" size="small" style="margin-left: 12px" @change="activeSymbol && loadBars(activeSymbol)">
            <el-radio-button value="qfq">前复权</el-radio-button>
            <el-radio-button value="hfq">后复权</el-radio-button>
            <el-radio-button value="raw">不复权</el-radio-button>
          </el-radio-group>
        </template>
        <div v-if="activeQuote" class="quote-bar">
          <span class="quote-name">{{ activeQuote.name }}</span>
          <span class="quote-price" :class="(activeQuote.pct_change || 0) >= 0 ? 'text-up' : 'text-down'">
            {{ activeQuote.last_price?.toFixed(2) }}
          </span>
          <span :class="(activeQuote.pct_change || 0) >= 0 ? 'text-up' : 'text-down'">
            {{ activeQuote.pct_change > 0 ? '+' : '' }}{{ activeQuote.pct_change?.toFixed(2) }}%
          </span>
          <span class="quote-sep" />
          <span>今开 <b>{{ activeQuote.open?.toFixed(2) || '--' }}</b></span>
          <span>最高 <b class="text-up">{{ activeQuote.high?.toFixed(2) || '--' }}</b></span>
          <span>最低 <b class="text-down">{{ activeQuote.low?.toFixed(2) || '--' }}</b></span>
          <span>昨收 <b>{{ activeQuote.pre_close?.toFixed(2) || '--' }}</b></span>
          <span class="quote-sep" />
          <span>成交额 <b>{{ (activeQuote.amount / 1e8).toFixed(2) }}亿</b></span>
          <span>量比 <b>{{ activeQuote.volume_ratio?.toFixed(2) || '--' }}</b></span>
        </div>
        <MinuteChart
          v-if="interval === 'minute' && minutePoints.length"
          :points="minutePoints"
          :pre-close="activeQuote?.pre_close || 0"
        />
        <el-empty
          v-else-if="interval === 'minute'"
          description="暂无分时数据（非交易时段或数据源暂不可用）"
        />
        <KlineChart v-else-if="displayBars.length" :bars="displayBars" />
        <el-empty v-else description="暂无数据（请先在系统页同步）" />
        <el-button
          v-if="activeSymbol"
          type="primary" plain size="small"
          style="margin-top: 10px"
          @click="runAnalyze"
        >
          AI 个股解读
        </el-button>
        <el-button
          v-if="activeSymbol"
          type="success" plain size="small"
          style="margin-top: 10px; margin-left: 8px"
          @click="runMultiagent"
        >
          多智能体分析
        </el-button>
      </el-card>

      <el-card shadow="never" style="margin-top: 12px">
        <template #header>
          打板候选池（涨停/连板）
          <span class="header-actions">
            <el-button link type="danger" size="small" :loading="boardRefreshing" @click="refreshBoardScreen">
              刷新
            </el-button>
          </span>
        </template>
        <div v-if="boardStatus" class="tip">{{ boardStatus }}</div>
        <div v-if="boardDate" class="tip" style="margin-bottom: 6px">
          数据日期：{{ boardDate }}
          <el-tag v-if="boardStale" type="warning" size="small">数据未更新到今天，点右上角「刷新」</el-tag>
        </div>
        <el-table :data="boardCandidates" size="small" max-height="340" highlight-current-row
                  @row-click="(row: any) => loadBars(row.symbol)">
          <el-table-column prop="name" label="名称" width="90" />
          <el-table-column label="涨幅" width="75">
            <template #default="{ row }">
              <span :class="(row.pct_change || 0) >= 0 ? 'text-up' : 'text-down'">
                {{ row.pct_change > 0 ? '+' : '' }}{{ row.pct_change?.toFixed(2) }}%
              </span>
            </template>
          </el-table-column>
          <el-table-column label="连板" width="60">
            <template #default="{ row }">
              <el-tag v-if="row.streak >= 1" :type="row.streak >= 3 ? 'danger' : 'warning'" size="small">
                {{ row.streak }}板
              </el-tag>
              <span v-else class="tip">首板</span>
            </template>
          </el-table-column>
          <el-table-column label="换手" width="65">
            <template #default="{ row }">{{ row.turnover?.toFixed(1) }}%</template>
          </el-table-column>
          <el-table-column label="成交额" width="75">
            <template #default="{ row }">{{ (row.amount / 1e8).toFixed(1) }}亿</template>
          </el-table-column>
          <el-table-column label="风险" width="75">
            <template #default="{ row }">
              <el-tag :type="row.risk === '高位风险' ? 'danger' : row.risk === '一字板' ? 'info' : 'success'" size="small">
                {{ row.risk }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!boardCandidates.length" description="暂无打板候选（今日涨停数少，或点刷新扫描）" :image-size="50" />
        <p class="tip" style="margin-top: 6px">筛选：今日涨幅≥7%（各板涨停线分别判定）；连板高度与换手质量评分。⚠️ 打板是极高风险操作，候选仅供参考，不构成操作建议</p>
      </el-card>
    </el-col>
  </el-row>

  <el-dialog v-model="analyzeDialog" :title="`${activeSymbol} AI 个股解读`" width="620px">
    <div v-loading="analyzeLoading" class="markdown-body" v-html="analyzeHtml" />
    <template #footer>
      <el-button @click="analyzeDialog = false">关闭</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="multiagentDialog" :title="`${activeSymbol} 多智能体分析`" width="720px">
    <div v-loading="multiagentLoading" class="markdown-body" v-html="multiagentHtml" />
    <template #footer>
      <el-button @click="multiagentDialog = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.search-row {
  display: flex;
  gap: 8px;
}

.tip {
  color: var(--app-text-secondary);
  font-size: 12px;
  margin-top: 8px;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
  color: #303133;
}

.section-subtitle {
  font-size: 12px;
  font-weight: 600;
  margin: 10px 0 6px;
  color: #606266;
}

.quote-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 8px 12px;
  margin-bottom: 8px;
  background: #f8f9fb;
  border-radius: 6px;
  font-size: 13px;
  color: #606266;
}

.quote-bar b {
  font-weight: 600;
  color: #303133;
}

.quote-name {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.quote-price {
  font-size: 18px;
  font-weight: 700;
}

.quote-sep {
  width: 1px;
  height: 14px;
  background: #dcdfe6;
}

.markdown-body {
  line-height: 1.8;
  font-size: 14px;
  max-height: 480px;
  overflow-y: auto;
  min-height: 120px;
}

.markdown-body :deep(h1) {
  font-size: 18px;
  border-bottom: 1px solid var(--app-border);
  padding-bottom: 6px;
}

.markdown-body :deep(h2) {
  font-size: 15px;
  margin-top: 12px;
}
</style>
