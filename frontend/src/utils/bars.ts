import type { KBar } from '../components/KlineChart.vue'

/** 日线 → 周/月/季/年线聚合（open=首根开盘，high/low=极值，close=末根收盘，volume=求和）。 */
export function resampleBars(bars: KBar[], period: 'week' | 'month' | 'quarter' | 'year'): KBar[] {
  if (!bars.length) return bars

  const keyOf = (date: string): string => {
    if (period === 'year') return date.slice(0, 4)
    if (period === 'quarter') return `${date.slice(0, 4)}Q${Math.ceil(parseInt(date.slice(5, 7), 10) / 3)}`
    if (period === 'month') return date.slice(0, 7)
    // week：以周一为分组键
    const dt = new Date(`${date}T00:00:00`)
    const day = (dt.getDay() + 6) % 7 // 周一=0
    dt.setDate(dt.getDate() - day)
    return dt.toISOString().slice(0, 10)
  }

  const groups: KBar[][] = []
  const index: Record<string, number> = {}
  for (const b of bars) {
    const key = keyOf(b.trade_date)
    let gi = index[key]
    if (gi === undefined) {
      gi = groups.length
      index[key] = gi
      groups.push([])
    }
    groups[gi].push(b)
  }

  return groups.map((g) => ({
    trade_date: g[0].trade_date, // 周期起始日作为标签
    open: g[0].open,
    high: Math.max(...g.map((b) => b.high)),
    low: Math.min(...g.map((b) => b.low)),
    close: g[g.length - 1].close,
    volume: g.reduce((sum, b) => sum + b.volume, 0),
  }))
}
