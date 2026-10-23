<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, ref } from 'vue'

import BaseChart from './BaseChart.vue'

export interface KBar {
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

const props = withDefaults(defineProps<{ bars: KBar[]; height?: string }>(), {
  height: '480px',
})

// 中国惯例：红涨绿跌
const UP = '#e03131'
const DOWN = '#2f9e44'
const MA_COLORS: Record<string, string> = {
  MA5: '#e8a33d', MA10: '#4f8fe0', MA20: '#9b6bd0', MA60: '#8c8c8c',
}
// 同花顺式副图：成交量固定 + 指标切换（MACD / KDJ / RSI）
const subIndicator = ref<'macd' | 'kdj' | 'rsi'>('macd')
// 快捷区间：默认最近 3 个月（免拖拽）
const rangeMode = ref<'1m' | '3m' | '6m' | '1y' | 'all'>('3m')
const RANGE_BARS: Record<string, number> = { '1m': 22, '3m': 66, '6m': 132, '1y': 250 }

// ---------------------------------------------------------------- 指标计算

function calcMA(n: number): (number | null)[] {
  const closes = props.bars.map((b) => b.close)
  const out: (number | null)[] = new Array(closes.length).fill(null)
  let sum = 0
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i]
    if (i >= n) sum -= closes[i - n]
    if (i >= n - 1) out[i] = +(sum / n).toFixed(3)
  }
  return out
}

function calcEMA(n: number): (number | null)[] {
  const closes = props.bars.map((b) => b.close)
  const out: (number | null)[] = new Array(closes.length).fill(null)
  const alpha = 2 / (n + 1)
  let prev: number | null = null
  for (let i = 0; i < closes.length; i++) {
    if (i < n - 1) continue
    if (prev === null) {
      prev = closes.slice(0, n).reduce((a, b) => a + b, 0) / n
    } else {
      prev = alpha * closes[i] + (1 - alpha) * prev
    }
    out[i] = +prev.toFixed(3)
  }
  return out
}

function calcBOLL(n = 20, k = 2): { upper: (number | null)[]; lower: (number | null)[] } {
  const closes = props.bars.map((b) => b.close)
  const upper: (number | null)[] = new Array(closes.length).fill(null)
  const lower: (number | null)[] = new Array(closes.length).fill(null)
  for (let i = n - 1; i < closes.length; i++) {
    const window = closes.slice(i - n + 1, i + 1)
    const m = window.reduce((a, b) => a + b, 0) / n
    const variance = window.reduce((a, b) => a + (b - m) ** 2, 0) / n
    const std = Math.sqrt(variance)
    upper[i] = +(m + k * std).toFixed(3)
    lower[i] = +(m - k * std).toFixed(3)
  }
  return { upper, lower }
}

function calcMACD(fast = 12, slow = 26, signal = 9): { dif: (number | null)[]; dea: (number | null)[]; hist: (number | null)[] } {
  const emaFast = calcEMA(fast)
  const emaSlow = calcEMA(slow)
  const dif: (number | null)[] = emaFast.map((v, i) =>
    v !== null && emaSlow[i] !== null ? +(v - emaSlow[i]!).toFixed(3) : null)
  const dea: (number | null)[] = new Array(dif.length).fill(null)
  const alpha = 2 / (signal + 1)
  let prev: number | null = null
  for (let i = 0; i < dif.length; i++) {
    if (dif[i] === null) continue
    const v = dif[i] as number
    if (prev === null) prev = v
    else prev = alpha * v + (1 - alpha) * prev
    dea[i] = +prev.toFixed(3)
  }
  const hist = dif.map((v, i) =>
    v !== null && dea[i] !== null ? +((v - dea[i]!) * 2).toFixed(3) : null)
  return { dif, dea, hist }
}

function calcKDJ(n = 9): { k: (number | null)[]; d: (number | null)[]; j: (number | null)[] } {
  const k: (number | null)[] = new Array(props.bars.length).fill(null)
  const d: (number | null)[] = new Array(props.bars.length).fill(null)
  const j: (number | null)[] = new Array(props.bars.length).fill(null)
  let kPrev = 50
  let dPrev = 50
  for (let i = 0; i < props.bars.length; i++) {
    if (i < n - 1) continue
    let hn = -Infinity
    let ln = Infinity
    for (let t = i - n + 1; t <= i; t++) {
      hn = Math.max(hn, props.bars[t].high)
      ln = Math.min(ln, props.bars[t].low)
    }
    const rsv = hn === ln ? 50 : ((props.bars[i].close - ln) / (hn - ln)) * 100
    kPrev = (2 / 3) * kPrev + (1 / 3) * rsv
    dPrev = (2 / 3) * dPrev + (1 / 3) * kPrev
    k[i] = +kPrev.toFixed(2)
    d[i] = +dPrev.toFixed(2)
    j[i] = +(3 * kPrev - 2 * dPrev).toFixed(2)
  }
  return { k, d, j }
}

function calcRSI(n = 14): (number | null)[] {
  const closes = props.bars.map((b) => b.close)
  const out: (number | null)[] = new Array(closes.length).fill(null)
  let avgGain = 0
  let avgLoss = 0
  for (let i = 1; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1]
    const gain = Math.max(change, 0)
    const loss = Math.max(-change, 0)
    if (i <= n) {
      avgGain += gain
      avgLoss += loss
      if (i === n) {
        avgGain /= n
        avgLoss /= n
        out[i] = avgLoss === 0 ? 100 : +(100 - 100 / (1 + avgGain / avgLoss)).toFixed(2)
      }
    } else {
      avgGain = (avgGain * (n - 1) + gain) / n
      avgLoss = (avgLoss * (n - 1) + loss) / n
      out[i] = avgLoss === 0 ? 100 : +(100 - 100 / (1 + avgGain / avgLoss)).toFixed(2)
    }
  }
  return out
}

// ---------------------------------------------------------------- 图表配置

const option = computed<EChartsOption>(() => {
  const dates = props.bars.map((b) => b.trade_date)
  const kData = props.bars.map((b) => [b.open, b.close, b.low, b.high])
  const volData = props.bars.map((b) => ({
    value: b.volume,
    itemStyle: { color: b.close >= b.open ? UP : DOWN },
  }))

  const maSeries = Object.entries(MA_COLORS).map(([name, color]) => ({
    name, type: 'line' as const, data: calcMA(parseInt(name.slice(2), 10)),
    smooth: true, symbol: 'none', lineStyle: { width: 1, color },
    itemStyle: { color },
  }))
  const boll = calcBOLL()
  const macd = calcMACD()
  const kdj = calcKDJ()
  const rsi = calcRSI()

  // 副图指标区（同花顺式：MACD/KDJ/RSI 切换）
  let subSeries: any[]
  if (subIndicator.value === 'kdj') {
    subSeries = [
      { name: 'K', type: 'line', data: kdj.k, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#e8a33d' } },
      { name: 'D', type: 'line', data: kdj.d, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#4f8fe0' } },
      { name: 'J', type: 'line', data: kdj.j, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#9b6bd0' } },
      { name: '超买80', type: 'line', data: dates.map(() => 80), xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, type: 'dashed', color: '#c0c4cc' }, silent: true },
      { name: '超卖20', type: 'line', data: dates.map(() => 20), xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, type: 'dashed', color: '#c0c4cc' }, silent: true },
    ]
  } else if (subIndicator.value === 'rsi') {
    subSeries = [
      { name: 'RSI14', type: 'line', data: rsi, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1.5, color: '#9b6bd0' } },
      { name: '超买70', type: 'line', data: dates.map(() => 70), xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, type: 'dashed', color: '#c0c4cc' }, silent: true },
      { name: '超卖30', type: 'line', data: dates.map(() => 30), xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, type: 'dashed', color: '#c0c4cc' }, silent: true },
    ]
  } else {
    const histData = macd.hist.map((v) => ({
      value: v === null ? null : Math.abs(v),
      itemStyle: { color: v !== null && v >= 0 ? UP : DOWN },
    }))
    subSeries = [
      { name: 'MACD柱', type: 'bar', data: histData, xAxisIndex: 2, yAxisIndex: 2, barWidth: '55%' },
      { name: 'DIF', type: 'line', data: macd.dif, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#e8a33d' } },
      { name: 'DEA', type: 'line', data: macd.dea, xAxisIndex: 2, yAxisIndex: 2, symbol: 'none', lineStyle: { width: 1, color: '#4f8fe0' } },
    ]
  }

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      valueFormatter: (v) => (typeof v === 'number' ? v.toLocaleString() : `${v}`),
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: {
      data: [...Object.keys(MA_COLORS), 'BOLL上轨', 'BOLL下轨'],
      top: 0, left: 'center',
      textStyle: { color: '#606266', fontSize: 11 },
      itemWidth: 14, itemHeight: 8,
    },
    grid: [
      { left: 62, right: 18, top: 26, height: '46%' },
      { left: 62, right: 18, top: '62%', height: '12%' },
      { left: 62, right: 18, top: '78%', height: '15%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#dcdfe6' } } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#dcdfe6' } } },
      { type: 'category', data: dates, gridIndex: 2, axisLabel: { color: '#909399', fontSize: 10 }, axisLine: { lineStyle: { color: '#dcdfe6' } } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { color: '#f0f2f5' } }, axisLabel: { color: '#909399' } },
      { gridIndex: 1, splitNumber: 1, axisLabel: { color: '#909399', formatter: (v: number) => (v / 100).toFixed(0) + '手' }, splitLine: { show: false } },
      { gridIndex: 2, scale: true, splitNumber: 2, axisLabel: { color: '#909399', fontSize: 9 }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 0, height: 16, start: 0, end: 100 },
    ],
    ...(() => {
      const total = props.bars.length
      if (rangeMode.value === 'all' || total === 0) return {}
      const n = Math.min(RANGE_BARS[rangeMode.value] || 66, total)
      const start = Math.max(((total - n) / total) * 100, 0)
      return {
        dataZoom: [
          { type: 'inside', xAxisIndex: [0, 1, 2], start, end: 100 },
          { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 0, height: 16, start, end: 100 },
        ],
      }
    })(),
    series: [
      {
        name: 'K线', type: 'candlestick', data: kData,
        itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
      },
      ...maSeries,
      { name: 'BOLL上轨', type: 'line', data: boll.upper, smooth: true, symbol: 'none', lineStyle: { width: 1, color: '#8ab4f8' }, itemStyle: { color: '#8ab4f8' } },
      { name: 'BOLL下轨', type: 'line', data: boll.lower, smooth: true, symbol: 'none', lineStyle: { width: 1, color: '#8ab4f8' }, itemStyle: { color: '#8ab4f8' } },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volData },
      ...subSeries,
    ],
  }
})
</script>

<template>
  <div>
    <div class="sub-switch">
      <el-radio-group v-model="subIndicator" size="small">
        <el-radio-button value="macd">MACD</el-radio-button>
        <el-radio-button value="kdj">KDJ</el-radio-button>
        <el-radio-button value="rsi">RSI</el-radio-button>
      </el-radio-group>
      <span class="sub-hint">副图指标</span>
      <el-radio-group v-model="rangeMode" size="small" style="margin-left: auto">
        <el-radio-button value="1m">近1月</el-radio-button>
        <el-radio-button value="3m">近3月</el-radio-button>
        <el-radio-button value="6m">近6月</el-radio-button>
        <el-radio-button value="1y">近1年</el-radio-button>
        <el-radio-button value="all">全部</el-radio-button>
      </el-radio-group>
    </div>
    <BaseChart :option="option" :height="height" />
  </div>
</template>

<style scoped>
.sub-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.sub-hint {
  color: var(--app-text-secondary);
  font-size: 12px;
}
</style>
