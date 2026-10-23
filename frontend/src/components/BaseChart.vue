<script setup lang="ts">
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { chartColors, currentTheme } from '../themes'

const props = withDefaults(defineProps<{ option: echarts.EChartsOption; height?: string }>(), {
  height: '320px',
})

const el = ref<HTMLElement>()
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

/** 主题适配：坐标轴/分割线/图例等中性色随主题切换（涨跌红绿保留原样）。 */
function patchOption(opt: echarts.EChartsOption): echarts.EChartsOption {
  const c = chartColors()
  const o: any = JSON.parse(JSON.stringify(opt))
  for (const key of ['xAxis', 'yAxis']) {
    const axes = Array.isArray(o[key]) ? o[key] : o[key] ? [o[key]] : []
    for (const axis of axes) {
      if (!axis) continue
      axis.axisLabel = { ...(axis.axisLabel || {}), color: axis.axisLabel?.color || c.axis }
      if (key === 'yAxis') {
        axis.splitLine = {
          ...(axis.splitLine || {}),
          lineStyle: { ...(axis.splitLine?.lineStyle || {}), color: c.split },
        }
      }
    }
  }
  if (o.legend && typeof o.legend === 'object' && !o.legend.textStyle?.color) {
    o.legend.textStyle = { ...(o.legend.textStyle || {}), color: c.text }
  }
  return o
}

function render() {
  chart?.setOption(patchOption(props.option), { notMerge: true })
}

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value)
  render()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(el.value)
})

watch(
  () => props.option,
  () => render(),
  { deep: true },
)

// 主题切换时重新应用图表配色
watch(currentTheme, () => render())

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="el" :style="{ height, width: '100%' }" />
</template>
