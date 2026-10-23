<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed } from 'vue'

import BaseChart from './BaseChart.vue'

export interface MinutePoint {
  time: string
  price: number
  volume: number
  avg: number
}

const props = withDefaults(defineProps<{
  points: MinutePoint[]
  preClose: number
  height?: string
}>(), { height: '480px' })

const UP = '#e03131'
const DOWN = '#2f9e44'

const option = computed<EChartsOption>(() => {
  const p = props.points
  const times = p.map((x) => x.time)
  const prices = p.map((x) => x.price)
  const avgs = p.map((x) => x.avg)
  const volData = p.map((x, i) => ({
    value: x.volume,
    itemStyle: { color: i > 0 && x.price >= prices[i - 1] ? UP : DOWN },
  }))
  const pre = props.preClose || prices[0] || 1
  const maxPct = Math.max(...prices.map((x) => Math.abs(x / pre - 1)), 0.005)

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      valueFormatter: (v) => (typeof v === 'number' ? v.toFixed(2) : `${v}`),
    },
    grid: [
      { left: 62, right: 58, top: 12, height: '64%' },
      { left: 62, right: 58, top: '80%', height: '14%' },
    ],
    xAxis: [
      { type: 'category', data: times, gridIndex: 0, boundaryGap: false, axisLabel: { show: false } },
      { type: 'category', data: times, gridIndex: 1, boundaryGap: false, axisLabel: { color: '#909399', fontSize: 10 } },
    ],
    yAxis: [
      {
        gridIndex: 0, scale: true,
        min: pre * (1 - maxPct), max: pre * (1 + maxPct),
        axisLabel: { color: '#909399' },
        splitLine: { lineStyle: { color: '#f0f2f5' } },
      },
      {
        gridIndex: 0, position: 'right', scale: true,
        min: -maxPct * 100, max: maxPct * 100,
        axisLabel: { color: '#909399', formatter: '{value}%' },
        splitLine: { show: false },
      },
      { gridIndex: 1, splitNumber: 1, axisLabel: { color: '#909399', fontSize: 9 }, splitLine: { show: false } },
    ],
    series: [
      {
        name: '价格', type: 'line', data: prices, symbol: 'none',
        lineStyle: { width: 1.5, color: '#2f6fed' },
        areaStyle: { color: 'rgba(47,111,237,0.06)' },
      },
      {
        name: '均价', type: 'line', data: avgs, symbol: 'none',
        lineStyle: { width: 1, color: '#e8a33d' },
      },
      {
        name: '昨收', type: 'line', data: times.map(() => pre), symbol: 'none',
        lineStyle: { width: 1, type: 'dashed', color: '#c0c4cc' }, silent: true,
      },
      { name: '成交量', type: 'bar', data: volData, xAxisIndex: 1, yAxisIndex: 2, barWidth: '60%' },
    ],
  }
})
</script>

<template>
  <BaseChart :option="option" :height="height" />
</template>
