<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'

import request from '../api/request'
import BaseChart from '../components/BaseChart.vue'

const account = ref<Record<string, any> | null>(null)
const orders = ref<Record<string, any>[]>([])
const equity = ref<{ date: string; equity: number }[]>([])
const orderDialog = ref(false)
const submitting = ref(false)

const orderForm = ref({
  symbol: '',
  direction: 'buy',
  order_type: 'market',
  price: 0,
  volume: 0,
  condition_type: '',
  trigger_price: 0,
})

const lastAiCheck = ref<Record<string, any> | null>(null)

// 同花顺导入：持仓/交割单两个导出文件（可只传一个）
const thsDialog = ref(false)
const thsPosFile = ref<File | null>(null)
const thsTradeFile = ref<File | null>(null)
const thsImporting = ref(false)

async function submitThsImport() {
  if (!thsPosFile.value && !thsTradeFile.value) {
    ElMessage.warning('请先选择持仓或交割单文件')
    return
  }
  thsImporting.value = true
  try {
    const fd = new FormData()
    if (thsPosFile.value) fd.append('positions_file', thsPosFile.value)
    if (thsTradeFile.value) fd.append('trades_file', thsTradeFile.value)
    const resp = (await request.post('/paper/import-ths', fd, { timeout: 60000 })) as {
      positions_imported: number
      trades_imported: number
      warnings: string[]
    }
    let msg = `导入完成：持仓 ${resp.positions_imported} 笔，交割单 ${resp.trades_imported} 笔`
    if (resp.warnings?.length) msg += `（${resp.warnings.join('；')}）`
    ElMessage.success(msg)
    thsDialog.value = false
    await load()
  } finally {
    thsImporting.value = false
  }
}

async function exportThs() {
  const blob = (await request.get('/paper/export-ths', { responseType: 'blob' })) as unknown as Blob
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = '唯我量化模拟账户.xlsx'
  a.click()
  URL.revokeObjectURL(url)
}

/** 打开下单框：重置为初始状态（不留上一笔信息）。 */
function openOrderDialog() {
  orderForm.value = {
    symbol: '', direction: 'buy', order_type: 'market', price: 0,
    volume: 0, condition_type: '', trigger_price: 0,
  }
  lastAiCheck.value = null
  orderDialog.value = true
}

async function load() {
  try {
    account.value = (await request.get('/paper/account')) as Record<string, any>
  } catch { account.value = null }
  try {
    orders.value = (await request.get('/paper/orders', { params: { limit: 50 } })) as Record<string, any>[]
  } catch { orders.value = [] }
  try {
    equity.value = (await request.get('/paper/equity')) as { date: string; equity: number }[]
  } catch { equity.value = [] }
}

async function submitOrder() {
  if (!orderForm.value.symbol) return
  submitting.value = true
  try {
    // AI 风控检查需调用大模型（约 10 秒），放宽超时
    const resp = (await request.post('/paper/orders', {
      symbol: orderForm.value.symbol,
      direction: orderForm.value.direction,
      volume: orderForm.value.volume,
      order_type: orderForm.value.order_type,
      price: orderForm.value.price,
      condition_type: orderForm.value.condition_type,
      trigger_price: orderForm.value.trigger_price,
    }, { timeout: 60000 })) as { order_id: number; status: string; ai_check: Record<string, any> }
    lastAiCheck.value = resp.ai_check || null
    orderDialog.value = false
    await load()
  } finally {
    submitting.value = false
  }
}

async function cancelOrder(id: number) {
  await request.delete(`/paper/orders/${id}`)
  await load()
}

// 名称自动补全：输入关键字 → 证券主表搜索
async function querySymbols(query: string, cb: (results: Array<{ value: string; name: string }>) => void) {
  if (!query.trim()) {
    cb([])
    return
  }
  try {
    const data = (await request.get('/market/instruments', {
      params: { keyword: query.trim() },
    })) as Array<{ symbol: string; name: string }>
    cb(data.map((r) => ({ value: r.symbol, name: r.name })))
  } catch {
    cb([])
  }
}

async function resetAccount() {
  await request.post('/paper/reset')
  await load()
}

const equityOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 64, right: 20, top: 20, bottom: 30 },
  xAxis: { type: 'category', data: equity.value.map((e) => e.date), axisLabel: { color: '#909399' } },
  yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { color: '#f0f2f5' } }, axisLabel: { color: '#909399' } },
  series: [{
    name: '净值', type: 'line', data: equity.value.map((e) => e.equity),
    smooth: true, symbol: 'none', lineStyle: { width: 2, color: '#2f6fed' },
  }],
}))

const STATUS_TAG: Record<string, string> = {
  submitted: 'info', filled: 'success', cancelled: 'warning', rejected: 'danger',
}

onMounted(load)
</script>

<template>
  <el-row :gutter="12">
    <el-col :span="6">
      <el-card shadow="never">
        <template #header>
          模拟账户
          <span class="header-actions">
            <el-button link type="primary" size="small" @click="resetAccount">重置</el-button>
          </span>
        </template>
        <template v-if="account">
          <div class="acct-total">{{ account.total?.toLocaleString() }}</div>
          <div class="acct-sub" :class="account.today_pnl >= 0 ? 'text-up' : 'text-down'">
            今日 {{ account.today_pnl >= 0 ? '+' : '' }}{{ account.today_pnl?.toLocaleString() }}
          </div>
          <el-descriptions :column="1" size="small" style="margin-top: 10px">
            <el-descriptions-item label="现金">{{ account.cash?.toLocaleString() }}</el-descriptions-item>
            <el-descriptions-item label="市值">{{ account.market_value?.toLocaleString() }}</el-descriptions-item>
            <el-descriptions-item label="累计盈亏">
              <span :class="account.total_pnl >= 0 ? 'text-up' : 'text-down'">{{ account.total_pnl?.toLocaleString() }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="AI 风控">{{ account.risk_mode }}</el-descriptions-item>
          </el-descriptions>
        </template>
        <el-empty v-else description="账户未初始化" :image-size="60" />
      </el-card>
    </el-col>

    <el-col :span="18">
      <el-card shadow="never">
        <template #header>
          持仓
          <span class="header-actions">
            <el-button size="small" @click="thsDialog = true">同花顺导入</el-button>
            <el-button size="small" @click="exportThs">导出</el-button>
            <el-button type="primary" size="small" @click="openOrderDialog">下单</el-button>
          </span>
        </template>
        <el-table :data="account?.positions || []" size="small">
          <el-table-column label="股票" width="150">
            <template #default="{ row }">
              <span>{{ row.name || '--' }}<span class="pos-symbol">（{{ row.symbol }}）</span></span>
            </template>
          </el-table-column>
          <el-table-column label="持仓/可用" width="130">
            <template #default="{ row }">{{ row.volume }} / {{ row.available_volume }}</template>
          </el-table-column>
          <el-table-column prop="avg_cost" label="成本价" width="90" />
          <el-table-column prop="price" label="现价" width="90" />
          <el-table-column prop="market_value" label="市值" width="110" />
          <el-table-column label="盈亏" width="140">
            <template #default="{ row }">
              <span :class="row.pnl >= 0 ? 'text-up' : 'text-down'">
                {{ row.pnl?.toLocaleString() }}（{{ (row.pnl_pct * 100).toFixed(2) }}%）
              </span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!account?.positions?.length" description="暂无持仓" :image-size="60" />
      </el-card>
    </el-col>
  </el-row>

  <el-row :gutter="12" style="margin-top: 12px">
    <el-col :span="12">
      <el-card shadow="never">
        <template #header>委托记录</template>
        <el-table :data="orders" size="small" max-height="320">
          <el-table-column label="股票" width="140">
            <template #default="{ row }">
              <span>{{ row.name || '--' }}<span class="pos-symbol">（{{ row.symbol }}）</span></span>
            </template>
          </el-table-column>
          <el-table-column label="方向" width="60">
            <template #default="{ row }">
              <el-tag :type="row.direction === 'buy' ? 'danger' : 'success'" size="small">
                {{ row.direction === 'buy' ? '买' : '卖' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="order_type" label="类型" width="70" />
          <el-table-column prop="volume" label="数量" width="80" />
          <el-table-column prop="filled_price" label="成交价" width="80" />
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="STATUS_TAG[row.status] || 'info'" size="small">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reject_reason" label="拒单原因" />
          <el-table-column label="操作" width="60">
            <template #default="{ row }">
              <el-button v-if="row.status === 'submitted'" link size="small" @click="cancelOrder(row.id)">撤单</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="12">
      <el-card shadow="never">
        <template #header>净值曲线</template>
        <BaseChart v-if="equity.length" :option="equityOption" height="320px" />
        <el-empty v-else description="暂无净值（收盘结算后生成）" :image-size="60" />
      </el-card>
    </el-col>
  </el-row>

  <el-dialog v-model="orderDialog" title="模拟下单" width="460px">
    <el-form label-width="80px">
      <el-form-item label="股票">
        <el-autocomplete
          v-model="orderForm.symbol"
          :fetch-suggestions="querySymbols"
          placeholder="输入代码或名称，如 600519 或 茅台"
          style="width: 100%"
          clearable
        >
          <template #default="{ item }">
            <span>{{ item.name }}（{{ item.value }}）</span>
          </template>
        </el-autocomplete>
      </el-form-item>
      <el-form-item label="方向">
        <el-radio-group v-model="orderForm.direction">
          <el-radio-button value="buy">买入</el-radio-button>
          <el-radio-button value="sell">卖出</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="类型">
        <el-radio-group v-model="orderForm.order_type">
          <el-radio-button value="market">市价</el-radio-button>
          <el-radio-button value="limit">限价</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="orderForm.order_type === 'limit'" label="价格">
        <el-input-number v-model="orderForm.price" :min="0.01" :precision="2" style="width: 100%" />
      </el-form-item>
      <el-form-item label="条件单">
        <el-select v-model="orderForm.condition_type" style="width: 100%">
          <el-option value="" label="无（立即有效）" />
          <el-option value="stop_loss" label="止损单（价格跌破触发价卖出）" />
          <el-option value="take_profit" label="止盈单（价格突破触发价卖出）" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="orderForm.condition_type" label="触发价">
        <el-input-number v-model="orderForm.trigger_price" :min="0.01" :precision="2" style="width: 100%" />
        <div class="tip">行情穿越触发价后按市价成交；条件单持续有效，可手动撤销</div>
      </el-form-item>
      <el-form-item label="数量(股)">
        <el-input-number v-model="orderForm.volume" :min="0" :step="100" style="width: 100%" />
        <div class="tip">买入填 0 = 市价全仓；A股整手 100 股，T+1 当日买入不可卖</div>
      </el-form-item>
    </el-form>
    <el-alert v-if="lastAiCheck && lastAiCheck.checked !== false" type="info" :closable="false" style="margin-bottom: 8px">
      <template #title>
        AI 风控：{{ lastAiCheck.approved ? '通过' : '拦截' }}（风险等级 {{ lastAiCheck.risk_level }}）
      </template>
      <ul class="reasons"><li v-for="(r, i) in lastAiCheck.reasons" :key="i">{{ r }}</li></ul>
    </el-alert>
    <template #footer>
      <el-button @click="orderDialog = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submitOrder">提交</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="thsDialog" title="同花顺导入" width="520px">
    <el-alert type="info" :closable="false" style="margin-bottom: 12px">
      <template #title>
        操作路径：同花顺远航版 → 交易 → 同花顺交易统一版 → 选「模拟炒股」→ 持仓页/交割单页点「导出」存为 Excel，再在此上传
      </template>
    </el-alert>
    <el-form label-width="90px">
      <el-form-item label="持仓文件">
        <el-upload
          :auto-upload="false"
          :limit="1"
          accept=".xls,.xlsx,.csv"
          :on-change="(f: any) => (thsPosFile = f.raw)"
          :on-remove="() => (thsPosFile = null)"
        >
          <el-button size="small">选择文件</el-button>
        </el-upload>
      </el-form-item>
      <el-form-item label="交割单文件">
        <el-upload
          :auto-upload="false"
          :limit="1"
          accept=".xls,.xlsx,.csv"
          :on-change="(f: any) => (thsTradeFile = f.raw)"
          :on-remove="() => (thsTradeFile = null)"
        >
          <el-button size="small">选择文件</el-button>
        </el-upload>
      </el-form-item>
    </el-form>
    <div class="tip">
      持仓导入为镜像覆盖（账户持仓 = 文件内容，现金不变）；交割单追加为已成交委托记录；两个文件可只传一个
    </div>
    <template #footer>
      <el-button @click="thsDialog = false">取消</el-button>
      <el-button type="primary" :loading="thsImporting" @click="submitThsImport">开始导入</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.acct-total {
  font-size: 26px;
  font-weight: 700;
}

.acct-sub {
  font-size: 13px;
  margin-top: 2px;
}

.tip {
  color: var(--app-text-secondary);
  font-size: 12px;
  margin-top: 4px;
}

.reasons {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
}

.pos-symbol {
  color: var(--app-text-secondary);
  font-size: 12px;
}
</style>
