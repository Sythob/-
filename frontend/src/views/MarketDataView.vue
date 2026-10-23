<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref, watch } from 'vue'

import request from '../api/request'
import BaseChart from '../components/BaseChart.vue'

const activeTab = ref('sectors')

// ---------------- 板块行情
const sectors = ref<Record<string, any>[]>([])
const sectorDialog = ref(false)
const sectorDetail = ref<{ name: string; stocks: Record<string, any>[] } | null>(null)
const loadingSectors = ref(false)

async function loadSectors() {
  loadingSectors.value = true
  try {
    sectors.value = (await request.get('/market/sectors')) as Record<string, any>[]
  } catch { sectors.value = [] } finally { loadingSectors.value = false }
}

async function openSector(row: Record<string, any>) {
  sectorDetail.value = null
  sectorDialog.value = true
  try {
    sectorDetail.value = (await request.get(`/market/sectors/${encodeURIComponent(row.name)}/stocks`)) as never
  } catch { sectorDetail.value = null }
}

// ---------------- 资金流向
const flowKind = ref<'industry' | 'concept'>('industry')
const flowRows = ref<Record<string, any>[]>([])
const loadingFlow = ref(false)

async function loadFundFlow() {
  loadingFlow.value = true
  try {
    flowRows.value = (await request.get('/market/fund-flow', { params: { kind: flowKind.value } })) as Record<string, any>[]
  } catch { flowRows.value = [] } finally { loadingFlow.value = false }
}

// ---------------- 龙虎榜
const lhb = ref<{ date: string; rows: Record<string, any>[] } | null>(null)
const loadingLhb = ref(false)

async function loadLhb() {
  loadingLhb.value = true
  try {
    lhb.value = (await request.get('/market/lhb')) as never
  } catch { lhb.value = null } finally { loadingLhb.value = false }
}

// ---------------- 北向资金
const northbound = ref<{ summary: Record<string, any>[]; hist: Record<string, any>[] } | null>(null)
const loadingNorth = ref(false)

async function loadNorthbound() {
  loadingNorth.value = true
  try {
    northbound.value = (await request.get('/market/northbound')) as never
  } catch { northbound.value = null } finally { loadingNorth.value = false }
}

const northOption = computed<EChartsOption>(() => {
  const hist = northbound.value?.hist || []
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 64, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: hist.map((h) => h.date), axisLabel: { color: '#909399' } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#f0f2f5' } }, axisLabel: { color: '#909399' } },
    series: [{
      name: '当日成交净买额(亿)', type: 'bar',
      data: hist.map((h) => Math.round(h.net / 1e8 * 100) / 100),
      itemStyle: { color: (p: any) => (p.value >= 0 ? '#e05a4e' : '#3c9e63') },
    }],
  }
})

/** 金额格式化：亿 / 万 */
function fmtAmount(v: number | undefined): string {
  if (!v) return '--'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(0)}万`
  return v.toFixed(0)
}

const UP = '#e05a4e'
const DOWN = '#3c9e63'

function pctColor(v: number | undefined): string {
  if (!v) return '#606266'
  return v > 0 ? UP : v < 0 ? DOWN : '#606266'
}

// 切到各页签时懒加载对应数据
watch(activeTab, (tab) => {
  if (tab === 'flow') loadFundFlow()
  else if (tab === 'lhb') loadLhb()
  else if (tab === 'north') loadNorthbound()
})

onMounted(loadSectors)
</script>

<template>
  <el-card shadow="never">
    <template #header>市场数据</template>
    <el-tabs v-model="activeTab">
      <!-- ================= 板块行情 ================= -->
      <el-tab-pane label="板块行情" name="sectors">
        <el-table v-loading="loadingSectors" :data="sectors" size="small" height="640" @row-click="openSector">
          <el-table-column prop="name" label="板块" width="120" />
          <el-table-column prop="company_count" label="家数" width="70" />
          <el-table-column label="涨跌幅" width="100" sortable :sort-by="(r: any) => r.pct_change">
            <template #default="{ row }">
              <span :style="{ color: pctColor(row.pct_change) }">
                {{ row.pct_change > 0 ? '+' : '' }}{{ row.pct_change?.toFixed(2) }}%
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="avg_price" label="平均价" width="90" />
          <el-table-column label="领涨股" min-width="140">
            <template #default="{ row }">
              {{ row.leader }}<span class="sub">（{{ row.leader_code }}）</span>
              <span class="sub" :style="{ color: pctColor(row.leader_pct) }"> +{{ row.leader_pct?.toFixed(2) }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="成交额" width="110">
            <template #default="{ row }">{{ fmtAmount(row.amount) }}</template>
          </el-table-column>
        </el-table>
        <div class="tip">点击板块行查看成分股（数据源：新浪，东财板块接口被禁用）</div>
      </el-tab-pane>

      <!-- ================= 资金流向 ================= -->
      <el-tab-pane label="资金流向" name="flow">
        <el-radio-group v-model="flowKind" size="small" style="margin-bottom: 10px" @change="loadFundFlow">
          <el-radio-button value="industry">行业</el-radio-button>
          <el-radio-button value="concept">概念</el-radio-button>
        </el-radio-group>
        <el-table v-loading="loadingFlow" :data="flowRows" size="small" height="620">
          <el-table-column prop="name" label="名称" width="130" />
          <el-table-column label="涨跌幅" width="100">
            <template #default="{ row }">
              <span :style="{ color: pctColor(row.change_pct) }">
                {{ row.change_pct > 0 ? '+' : '' }}{{ row.change_pct?.toFixed(2) }}%
              </span>
            </template>
          </el-table-column>
          <el-table-column label="流入资金" width="110">
            <template #default="{ row }">
              <span :style="{ color: UP }">{{ fmtAmount(row.inflow) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="流出资金" width="110">
            <template #default="{ row }">
              <span :style="{ color: DOWN }">{{ fmtAmount(row.outflow) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="净流入" width="110">
            <template #default="{ row }">
              <span :style="{ color: pctColor(row.net) }">{{ fmtAmount(row.net) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="count" label="家数" width="70" />
          <el-table-column label="领涨股" min-width="130">
            <template #default="{ row }">
              {{ row.leader }}
              <span class="sub" :style="{ color: pctColor(row.leader_pct) }"> +{{ row.leader_pct?.toFixed(2) }}%</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="tip">即时资金流排行（数据源：东方财富）</div>
      </el-tab-pane>

      <!-- ================= 龙虎榜 ================= -->
      <el-tab-pane label="龙虎榜" name="lhb">
        <div v-if="lhb" style="margin-bottom: 8px">
          交易日：<b>{{ lhb.date }}</b>，共 {{ lhb.rows.length }} 只上榜
          <el-button link type="primary" size="small" @click="loadLhb">刷新</el-button>
        </div>
        <el-table v-loading="loadingLhb" :data="lhb?.rows || []" size="small" height="620">
          <el-table-column prop="code" label="代码" width="90" />
          <el-table-column prop="name" label="名称" width="120" />
          <el-table-column prop="close" label="收盘价" width="90" />
          <el-table-column label="涨跌幅" width="90">
            <template #default="{ row }">
              <span :style="{ color: pctColor(row.change_pct) }">{{ row.change_pct?.toFixed(2) }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="成交量(万)" width="110">
            <template #default="{ row }">{{ row.volume_wan?.toFixed(0) }}</template>
          </el-table-column>
          <el-table-column label="成交额(万)" width="110">
            <template #default="{ row }">{{ row.amount_wan?.toFixed(0) }}</template>
          </el-table-column>
          <el-table-column prop="reason" label="上榜原因" min-width="180" />
        </el-table>
        <el-empty v-if="!lhb && !loadingLhb" description="暂无龙虎榜数据" :image-size="60" />
        <div class="tip">数据源：新浪（东财龙虎榜接口被禁用）；龙虎榜约 17:30 后更新</div>
      </el-tab-pane>

      <!-- ================= 北向资金 ================= -->
      <el-tab-pane label="北向资金" name="north">
        <el-alert type="warning" :closable="false" style="margin-bottom: 10px">
          <template #title>2024 年 8 月起沪深港通已停发盘中实时数据，仅收盘后公布日度数据（净买额可能为 0 或次日更新）</template>
        </el-alert>
        <el-row v-if="northbound" :gutter="12" style="margin-bottom: 12px">
          <el-col v-for="s in northbound.summary" :key="s.board" :span="8">
            <el-card shadow="never" class="north-card">
              <div class="north-title">{{ s.board }} · {{ s.date }}</div>
              <div class="north-net" :style="{ color: pctColor(s.net) }">
                {{ s.net > 0 ? '+' : '' }}{{ (s.net / 1e8).toFixed(2) }}亿
              </div>
              <div class="north-sub">
                {{ s.index }} {{ s.index_pct > 0 ? '+' : '' }}{{ s.index_pct?.toFixed(2) }}% · 涨{{ s.up }} 平{{ s.flat }} 跌{{ s.down }}
              </div>
            </el-card>
          </el-col>
        </el-row>
        <BaseChart v-if="northbound?.hist?.length" :option="northOption" height="300px" />
        <el-empty v-if="!northbound && !loadingNorth" description="暂无北向资金数据" :image-size="60" />
        <div class="tip">数据源：东方财富（部分接口可用）</div>
      </el-tab-pane>
    </el-tabs>
  </el-card>

  <!-- 板块成分股弹窗 -->
  <el-dialog v-model="sectorDialog" :title="`${sectorDetail?.name || ''} 成分股`" width="640px">
    <el-table v-if="sectorDetail" :data="sectorDetail.stocks" size="small" max-height="480">
      <el-table-column prop="code" label="代码" width="90" />
      <el-table-column prop="name" label="名称" width="120" />
      <el-table-column prop="price" label="最新价" width="90" />
      <el-table-column label="涨跌幅" width="100">
        <template #default="{ row }">
          <span :style="{ color: pctColor(row.change_pct) }">
            {{ row.change_pct > 0 ? '+' : '' }}{{ row.change_pct?.toFixed(2) }}%
          </span>
        </template>
      </el-table-column>
      <el-table-column label="成交额" width="100">
        <template #default="{ row }">{{ fmtAmount(row.amount) }}</template>
      </el-table-column>
    </el-table>
    <el-empty v-else description="成分股加载失败，请重试" :image-size="60" />
  </el-dialog>
</template>

<style scoped>
.sub {
  color: var(--app-text-secondary);
  font-size: 12px;
  margin-left: 4px;
}

.tip {
  color: var(--app-text-secondary);
  font-size: 12px;
  margin-top: 8px;
}

.north-card {
  text-align: center;
}

.north-title {
  font-size: 13px;
  color: var(--app-text-secondary);
}

.north-net {
  font-size: 24px;
  font-weight: 700;
  margin: 6px 0;
}

.north-sub {
  font-size: 12px;
  color: var(--app-text-secondary);
}
</style>
