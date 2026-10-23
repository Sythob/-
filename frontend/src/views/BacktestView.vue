<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'

import request from '../api/request'
import BaseChart from '../components/BaseChart.vue'

interface StrategyMeta {
  name: string
  display_name: string
  description: string
  interval: string
  params: Record<string, any>
  params_schema: Array<{
    name: string
    type: string
    default: number
    min?: number
    max?: number
    label: string
  }>
}

interface BacktestDetail {
  id: number
  strategy_name: string
  params: Record<string, any>
  universe: string[]
  start_date: string
  end_date: string
  capital: number
  status: string
  error: string
  metrics: Record<string, number | null>
  equity: Array<{ date: string; equity: number; cash: number; return_rate: number }>
  trades: Array<Record<string, any>>
}

// ---- 智能回测 ----
const btTab = ref<'manual' | 'auto'>('manual')
const autoResults = ref<Record<string, any>[]>([])
const autoRunning = ref(false)
const autoStatus = ref('')
const autoOrderBy = ref('total_return')
let autoTimer: number | undefined

async function loadAutoResults() {
  try {
    autoResults.value = (await request.get('/backtest/auto', {
      params: { order_by: autoOrderBy.value },
    })) as Record<string, any>[]
  } catch { autoResults.value = [] }
}

async function runAutoBacktest() {
  autoRunning.value = true
  autoStatus.value = '批量回测中：8 组策略 × 自选池标的（约 2~5 分钟）...'
  try {
    const resp = (await request.post('/backtest/auto')) as { task_id: string }
    autoTimer = window.setInterval(async () => {
      try {
        const task = (await request.get(`/system/tasks/${resp.task_id}`)) as Record<string, any>
        if (task.status === 'done') {
          window.clearInterval(autoTimer)
          autoRunning.value = false
          const r = task.result
          autoStatus.value = `完成：新跑 ${r.ran} 组，跳过 ${r.skipped} 组，组合回测 ${r.portfolio} 组`
          await loadAutoResults()
        } else if (task.status === 'failed') {
          window.clearInterval(autoTimer)
          autoRunning.value = false
          autoStatus.value = `失败：${task.error || '未知错误'}`
        }
      } catch { /* 轮询失败重试 */ }
    }, 5000)
  } catch {
    autoRunning.value = false
    autoStatus.value = '任务提交失败'
  }
}

function viewAutoDetail(row: Record<string, any>) {
  // 用该组合的参数跑一次完整回测，展示在结果面板
  form.value.strategy = row.strategy
  form.value.symbols = row.symbol === '组合' ? '' : row.symbol
  form.value.start = row.start_date
  form.value.params = { ...row.params }
  if (row.symbol === '组合') {
    form.value.allocation = 'equal'
    form.value.target_positions = 3
  }
  btTab.value = 'manual'
  submit()
}

const strategies = ref<StrategyMeta[]>([])
const generated = ref<{ id: number; name: string }[]>([])
const runs = ref<Record<string, any>[]>([])

const form = ref({
  strategy: 'dual_ma',
  symbols: '',
  start: '2022-01-01',
  end: '',
  capital: 1000000,
  params: {} as Record<string, any>,
  benchmark: false,
  allocation: 'full',
  fixed_fraction: 0.3,
  target_positions: 5,
  kelly_win_rate: 0.5,
  kelly_payoff: 1.0,
  interval: 'daily',
  max_loss_per_trade: 0,
  max_daily_loss: 0,
})

// ---- 参数优化 ----
const optimizeDialog = ref(false)
const gridText = ref('')
const optimizeMetric = ref('total_return')
const optimizeRunning = ref(false)
const optimizeResults = ref<Array<Record<string, any>>>([])
const optimizeWarning = ref('')
let optimizeTimer: number | undefined

function openOptimize() {
  const meta = selectedMeta.value
  if (meta?.params_schema?.length) {
    const grid: Record<string, number[]> = {}
    for (const p of meta.params_schema) {
      if (p.type === 'int' && p.min !== undefined && p.max !== undefined) {
        const step = Math.max(Math.ceil((p.max - p.min) / 3), 1)
        const values: number[] = []
        for (let v = p.min; v <= p.max && values.length < 5; v += step) values.push(v)
        grid[p.name] = values
      }
    }
    gridText.value = JSON.stringify(grid, null, 2)
  } else {
    gridText.value = '{\n  "param_name": [1, 2, 3]\n}'
  }
  optimizeResults.value = []
  optimizeWarning.value = ''
  optimizeDialog.value = true
}

async function runOptimize() {
  let grid: Record<string, number[]>
  try {
    grid = JSON.parse(gridText.value)
  } catch {
    return
  }
  const symbols = form.value.symbols.split(/[,，\s]+/).filter(Boolean)
  if (!symbols.length) return
  optimizeRunning.value = true
  optimizeResults.value = []
  try {
    const resp = (await request.post('/backtest/optimize', {
      strategy: form.value.strategy,
      symbols,
      start: form.value.start,
      end: form.value.end,
      capital: form.value.capital,
      base_params: form.value.params,
      grid,
      metric: optimizeMetric.value,
      allocation: form.value.allocation,
    })) as { task_id: string }
    optimizeTimer = window.setInterval(async () => {
      const task = (await request.get(`/system/tasks/${resp.task_id}`)) as Record<string, any>
      if (task.status === 'done') {
        window.clearInterval(optimizeTimer)
        optimizeRunning.value = false
        optimizeResults.value = task.result.results
        optimizeWarning.value = task.result.warning || ''
      } else if (task.status === 'failed') {
        window.clearInterval(optimizeTimer)
        optimizeRunning.value = false
      }
    }, 2000)
  } catch {
    optimizeRunning.value = false
  }
}

function openReport() {
  if (currentRun.value) {
    window.open(`/api/backtest/runs/${currentRun.value.id}/report`, '_blank')
  }
}

const selectedMeta = computed(() => strategies.value.find((s) => s.name === form.value.strategy))
const currentRun = ref<BacktestDetail | null>(null)
const running = ref(false)
let pollTimer: number | undefined

function onStrategyChange() {
  const meta = selectedMeta.value
  form.value.params = {}
  if (meta) {
    for (const p of meta.params_schema) form.value.params[p.name] = p.default
  }
}

async function loadStrategies() {
  strategies.value = (await request.get('/strategies')) as StrategyMeta[]
  try {
    generated.value = (await request.get('/strategies/generated')) as { id: number; name: string }[]
    for (const g of generated.value) {
      strategies.value.push({
        name: g.name, display_name: `${g.name}（AI 生成）`, description: '', interval: 'daily',
        params: {}, params_schema: [],
      })
    }
  } catch { /* 无生成策略 */ }
  onStrategyChange()
  runs.value = (await request.get('/backtest/runs')) as Record<string, any>[]
}

async function submit() {
  const symbols = form.value.symbols.split(/[,，\s]+/).filter(Boolean)
  if (!symbols.length) return
  running.value = true
  try {
    const resp = (await request.post('/backtest/run', {
      strategy: form.value.strategy,
      symbols,
      start: form.value.start,
      end: form.value.end,
      capital: form.value.capital,
      params: form.value.params,
      benchmark: form.value.benchmark,
      allocation: form.value.allocation,
      fixed_fraction: form.value.fixed_fraction,
      target_positions: form.value.target_positions,
      kelly_win_rate: form.value.kelly_win_rate,
      kelly_payoff: form.value.kelly_payoff,
      interval: form.value.interval,
      max_loss_per_trade_pct: form.value.max_loss_per_trade / 100,
      max_daily_loss_pct: form.value.max_daily_loss / 100,
    })) as { run_id: number }
    pollResult(resp.run_id)
  } finally {
    running.value = false
  }
}

function pollResult(runId: number) {
  pollTimer = window.setInterval(async () => {
    try {
      const detail = (await request.get(`/backtest/runs/${runId}`)) as BacktestDetail
      if (detail.status === 'done' || detail.status === 'failed') {
        window.clearInterval(pollTimer)
        currentRun.value = detail
        const list = (await request.get('/backtest/runs')) as Record<string, any>[]
        runs.value = list
      }
    } catch { /* 轮询失败忽略，下一轮重试 */ }
  }, 1500)
}

const equityOption = computed<EChartsOption>(() => {
  const run = currentRun.value
  if (!run) return {}
  const dates = run.equity.map((e) => e.date)
  const equityData = run.equity.map((e) => e.equity)
  let peak = -Infinity
  const drawdownData = equityData.map((v) => {
    peak = Math.max(peak, v)
    return peak > 0 ? +((v / peak - 1) * 100).toFixed(2) : 0
  })
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['净值', '回撤(%)'], top: 0 },
    grid: { left: 64, right: 20, top: 32, bottom: 30 },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#909399' } },
    yAxis: [
      { type: 'value', scale: true, splitLine: { lineStyle: { color: '#f0f2f5' } }, axisLabel: { color: '#909399' } },
      { type: 'value', axisLabel: { color: '#909399', formatter: '{value}%' }, splitLine: { show: false } },
    ],
    series: [
      {
        name: '净值', type: 'line', yAxisIndex: 0, data: equityData,
        smooth: true, symbol: 'none', lineStyle: { width: 2, color: '#2f6fed' },
      },
      {
        name: '回撤(%)', type: 'line', yAxisIndex: 1, data: drawdownData,
        smooth: true, symbol: 'none', lineStyle: { width: 1, color: '#e03131' },
        areaStyle: { color: 'rgba(224,49,49,0.06)' },
      },
    ],
  }
})

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '--'
  return `${v > 0 ? '+' : ''}${(v * 100).toFixed(2)}%`
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return '--'
  return v.toFixed(digits)
}

onMounted(loadStrategies)
</script>

<template>
  <el-tabs v-model="btTab" @tab-change="(name: any) => name === 'auto' && loadAutoResults()">
    <el-tab-pane label="手动回测" name="manual">
      <el-row :gutter="12">
    <el-col :span="7">
      <el-card shadow="never">
        <template #header>新建回测</template>
        <el-form label-width="90px" size="small">
          <el-form-item label="策略">
            <el-select v-model="form.strategy" style="width: 100%" @change="onStrategyChange">
              <el-option v-for="s in strategies" :key="s.name" :value="s.name" :label="s.display_name" />
            </el-select>
          </el-form-item>
          <el-form-item label="标的代码">
            <el-input v-model="form.symbols" placeholder="逗号分隔，如 600519,000001" />
          </el-form-item>
          <el-form-item label="开始日期">
            <el-date-picker v-model="form.start" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
          </el-form-item>
          <el-form-item label="结束日期">
            <el-date-picker v-model="form.end" type="date" value-format="YYYY-MM-DD" placeholder="留空=最新" style="width: 100%" />
          </el-form-item>
          <el-form-item label="初始资金">
            <el-input-number v-model="form.capital" :min="10000" :step="100000" style="width: 100%" />
          </el-form-item>
          <template v-if="selectedMeta">
            <el-form-item v-for="p in selectedMeta.params_schema" :key="p.name" :label="p.label || p.name">
              <el-input-number
                v-if="p.type === 'int'"
                v-model="form.params[p.name]"
                :min="p.min" :max="p.max" :step="1" style="width: 100%"
              />
              <el-input-number
                v-else-if="p.type === 'float'"
                v-model="form.params[p.name]"
                :min="p.min" :max="p.max" :step="0.1" style="width: 100%"
              />
              <el-input v-else v-model="form.params[p.name]" style="width: 100%" />
            </el-form-item>
          </template>
          <el-form-item label="数据周期">
            <el-radio-group v-model="form.interval" size="small">
              <el-radio-button value="daily">日线</el-radio-button>
              <el-radio-button value="60m">60分钟</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="单笔止损">
            <el-input-number v-model="form.max_loss_per_trade" :min="0" :max="50" :step="1" style="width: 100%" />
            <div class="form-tip">持仓亏损超过 X% 强制卖出（0 = 关闭）</div>
          </el-form-item>
          <el-form-item label="单日限额">
            <el-input-number v-model="form.max_daily_loss" :min="0" :max="30" :step="1" style="width: 100%" />
            <div class="form-tip">权益较前日跌超 X% 次日暂停开仓（0 = 关闭）</div>
          </el-form-item>
          <el-form-item label="基准对比">
            <el-switch v-model="form.benchmark" active-text="沪深300" />
          </el-form-item>
          <el-form-item label="仓位分配">
            <el-select v-model="form.allocation" style="width: 100%">
              <el-option value="full" label="全仓（单标的集中）" />
              <el-option value="equal" label="等权（组合分散）" />
              <el-option value="fixed" label="固定比例" />
              <el-option value="kelly" label="半凯利（保守）" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="form.allocation === 'fixed'" label="单笔比例">
            <el-input-number v-model="form.fixed_fraction" :min="0.05" :max="1" :step="0.05" style="width: 100%" />
          </el-form-item>
          <el-form-item v-if="form.allocation === 'equal'" label="目标持仓数">
            <el-input-number v-model="form.target_positions" :min="1" :max="20" style="width: 100%" />
          </el-form-item>
          <template v-if="form.allocation === 'kelly'">
            <el-form-item label="胜率估计">
              <el-input-number v-model="form.kelly_win_rate" :min="0.1" :max="0.9" :step="0.05" style="width: 100%" />
            </el-form-item>
            <el-form-item label="盈亏比估计">
              <el-input-number v-model="form.kelly_payoff" :min="0.2" :max="5" :step="0.1" style="width: 100%" />
            </el-form-item>
          </template>
          <el-button type="primary" :loading="running" style="width: 100%" @click="submit">
            开始回测
          </el-button>
          <el-button style="width: 100%; margin-left: 0; margin-top: 8px" @click="openOptimize">
            参数网格优化
          </el-button>
        </el-form>
      </el-card>

      <el-card shadow="never" style="margin-top: 12px">
        <template #header>回测历史</template>
        <el-table :data="runs" size="small" max-height="300">
          <el-table-column prop="id" label="ID" width="50" />
          <el-table-column prop="strategy_name" label="策略" width="90" />
          <el-table-column prop="start_date" label="开始" width="90" />
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.status === 'done' ? 'success' : row.status === 'failed' ? 'danger' : 'info'" size="small">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="60">
            <template #default="{ row }">
              <el-button link size="small" type="primary" @click="pollResult(row.id)">查看</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-col>

    <el-col :span="17">
      <el-card shadow="never" v-if="currentRun">
        <template #header>
          <span>回测结果 #{{ currentRun.id }} — {{ currentRun.strategy_name }}</span>
          <el-tag v-if="currentRun.status === 'failed'" type="danger" size="small">{{ currentRun.error }}</el-tag>
          <span class="header-actions">
            <el-button v-if="currentRun.status === 'done'" size="small" type="primary" @click="openReport">
              完整 HTML 报告
            </el-button>
          </span>
        </template>
        <el-row :gutter="12" v-if="currentRun.metrics">
          <el-col :span="4">
            <div class="metric"><div class="metric-label">总收益</div><div class="metric-value">{{ fmtPct(currentRun.metrics.total_return) }}</div></div>
          </el-col>
          <el-col :span="4">
            <div class="metric"><div class="metric-label">年化收益</div><div class="metric-value">{{ fmtPct(currentRun.metrics.annualized_return) }}</div></div>
          </el-col>
          <el-col :span="4">
            <div class="metric"><div class="metric-label">最大回撤</div><div class="metric-value text-down">{{ fmtPct(currentRun.metrics.max_drawdown) }}</div></div>
          </el-col>
          <el-col :span="4">
            <div class="metric"><div class="metric-label">夏普比率</div><div class="metric-value">{{ fmtNum(currentRun.metrics.sharpe_ratio) }}</div></div>
          </el-col>
          <el-col :span="4">
            <div class="metric"><div class="metric-label">胜率</div><div class="metric-value">{{ fmtPct(currentRun.metrics.win_rate) }}</div></div>
          </el-col>
          <el-col :span="4">
            <div class="metric"><div class="metric-label">交易次数</div><div class="metric-value">{{ currentRun.metrics.trade_count }}</div></div>
          </el-col>
        </el-row>

        <BaseChart v-if="currentRun.equity?.length" :option="equityOption" height="300px" style="margin-top: 12px" />

        <el-tabs style="margin-top: 12px">
          <el-tab-pane label="成交明细">
            <el-table :data="currentRun.trades" size="small" max-height="320">
              <el-table-column prop="trade_time" label="时间" width="110" />
              <el-table-column prop="symbol" label="代码" width="80" />
              <el-table-column label="方向" width="60">
                <template #default="{ row }">
                  <el-tag :type="row.direction === 'buy' ? 'danger' : 'success'" size="small">
                    {{ row.direction === 'buy' ? '买入' : '卖出' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="price" label="成交价" width="90" />
              <el-table-column prop="volume" label="数量(股)" width="90" />
              <el-table-column prop="commission" label="费用" width="90" />
              <el-table-column label="盈亏" width="100">
                <template #default="{ row }">
                  <span :class="(row.pnl || 0) >= 0 ? 'text-up' : 'text-down'">
                    {{ row.pnl ? row.pnl.toFixed(2) : '--' }}
                  </span>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="指标明细">
            <el-descriptions :column="2" size="small">
              <el-descriptions-item v-for="(v, k) in currentRun.metrics" :key="k" :label="k">
                {{ typeof v === 'number' ? v.toFixed(4) : v }}
              </el-descriptions-item>
            </el-descriptions>
          </el-tab-pane>
        </el-tabs>
      </el-card>
      <el-card v-else shadow="never">
        <el-empty description="提交回测后在此查看结果" />
      </el-card>
    </el-col>
  </el-row>
    </el-tab-pane>

    <el-tab-pane label="智能回测" name="auto">
      <el-card shadow="never">
        <template #header>
          智能批量回测战绩（8 组策略 × 自选池，近 5 年区间）
          <span class="header-actions">
            <el-button type="primary" size="small" :loading="autoRunning" @click="runAutoBacktest">
              运行批量回测
            </el-button>
          </span>
        </template>
        <div v-if="autoStatus" class="auto-status">{{ autoStatus }}</div>
        <el-table :data="autoResults" size="small" max-height="560" highlight-current-row @row-click="viewAutoDetail">
          <el-table-column prop="strategy_name" label="策略" width="130" />
          <el-table-column prop="symbol" label="标的" width="90" />
          <el-table-column label="胜率" width="80" sortable :sort-method="(a: any, b: any) => a.win_rate - b.win_rate">
            <template #default="{ row }">
              <span :class="row.win_rate >= 0.5 ? 'text-up' : 'text-down'">{{ (row.win_rate * 100).toFixed(1) }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="总收益率" width="100" sortable :sort-method="(a: any, b: any) => a.total_return - b.total_return">
            <template #default="{ row }">
              <span :class="row.total_return >= 0 ? 'text-up' : 'text-down'">{{ (row.total_return * 100).toFixed(2) }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="年化" width="90" sortable :sort-method="(a: any, b: any) => a.annualized_return - b.annualized_return">
            <template #default="{ row }">{{ (row.annualized_return * 100).toFixed(2) }}%</template>
          </el-table-column>
          <el-table-column label="最大回撤" width="95" sortable :sort-method="(a: any, b: any) => a.max_drawdown - b.max_drawdown">
            <template #default="{ row }">
              <span class="text-down">{{ (row.max_drawdown * 100).toFixed(2) }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="夏普" width="70" sortable :sort-method="(a: any, b: any) => a.sharpe_ratio - b.sharpe_ratio">
            <template #default="{ row }">{{ row.sharpe_ratio?.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="trade_count" label="交易数" width="70" />
          <el-table-column label="参数" min-width="120">
            <template #default="{ row }">
              <span class="params-text">{{ JSON.stringify(row.params) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="run_at" label="跑批时间" width="130" />
        </el-table>
        <el-empty v-if="!autoResults.length" description="暂无战绩，点击右上角「运行批量回测」" />
        <p class="form-tip" style="margin-top: 8px">
          点击任意行 → 用该组合参数跑一次完整回测查看净值/成交明细。24 小时内同组合不重复计算。
        </p>
      </el-card>
    </el-tab-pane>
  </el-tabs>

  <el-dialog v-model="optimizeDialog" title="参数网格优化" width="720px">
    <el-alert
      type="info" :closable="false"
      title="自动生成当前策略的参数网格（可手动编辑），后台遍历所有组合回测后按指标排序"
      style="margin-bottom: 10px"
    />
    <el-row :gutter="10">
      <el-col :span="12">
        <el-input v-model="gridText" type="textarea" :rows="8" placeholder='{"fast": [3, 5, 10], "slow": [20, 60]}' />
      </el-col>
      <el-col :span="12">
        <el-form label-width="90px" size="small">
          <el-form-item label="排序指标">
            <el-select v-model="optimizeMetric" style="width: 100%">
              <el-option value="total_return" label="总收益率" />
              <el-option value="annualized_return" label="年化收益率" />
              <el-option value="sharpe_ratio" label="夏普比率" />
              <el-option value="max_drawdown" label="最大回撤（升序）" />
              <el-option value="win_rate" label="胜率" />
            </el-select>
          </el-form-item>
          <el-alert
            v-if="optimizeWarning" type="warning" :closable="false"
            :title="optimizeWarning" style="margin-top: 8px"
          />
        </el-form>
      </el-col>
    </el-row>
    <el-table v-if="optimizeResults.length" :data="optimizeResults" size="small" max-height="360" style="margin-top: 10px">
      <el-table-column type="index" label="#" width="40" />
      <el-table-column label="参数" min-width="180">
        <template #default="{ row }">
          <span class="params-text">{{ JSON.stringify(row.params) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="总收益" width="90">
        <template #default="{ row }">{{ (row.metrics.total_return * 100).toFixed(2) }}%</template>
      </el-table-column>
      <el-table-column label="年化" width="90">
        <template #default="{ row }">{{ (row.metrics.annualized_return * 100).toFixed(2) }}%</template>
      </el-table-column>
      <el-table-column label="最大回撤" width="90">
        <template #default="{ row }">{{ (row.metrics.max_drawdown * 100).toFixed(2) }}%</template>
      </el-table-column>
      <el-table-column label="夏普" width="70">
        <template #default="{ row }">{{ row.metrics.sharpe_ratio?.toFixed(2) }}</template>
      </el-table-column>
      <el-table-column label="胜率" width="70">
        <template #default="{ row }">{{ (row.metrics.win_rate * 100).toFixed(0) }}%</template>
      </el-table-column>
    </el-table>
    <template #footer>
      <el-button @click="optimizeDialog = false">关闭</el-button>
      <el-button type="primary" :loading="optimizeRunning" @click="runOptimize">开始优化</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.metric {
  text-align: center;
  padding: 10px 0;
  background: #f8f9fb;
  border-radius: 6px;
}

.metric-label {
  color: var(--app-text-secondary);
  font-size: 12px;
}

.metric-value {
  font-size: 18px;
  font-weight: 600;
  margin-top: 4px;
}

.params-text {
  font-family: monospace;
  font-size: 12px;
}

.form-tip {
  color: var(--app-text-secondary);
  font-size: 12px;
  line-height: 1.4;
}

.auto-status {
  color: #606266;
  font-size: 13px;
  margin-bottom: 8px;
}
</style>
