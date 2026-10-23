<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

import request from '../api/request'

const config = ref<Record<string, any>>({})
const scheduler = ref<{ running: boolean; jobs: Array<Record<string, any>> }>({ running: false, jobs: [] })
const tasks = ref<Array<Record<string, any>>>([])
const syncing = ref(false)
const syncTaskId = ref('')
let timer: number | undefined

async function load() {
  try {
    config.value = (await request.get('/system/config')) as Record<string, any>
  } catch { config.value = {} }
  try {
    scheduler.value = (await request.get('/system/scheduler')) as { running: boolean; jobs: Array<Record<string, any>> }
  } catch { scheduler.value = { running: false, jobs: [] } }
  try {
    tasks.value = (await request.get('/system/tasks')) as Array<Record<string, any>>
  } catch { tasks.value = [] }
}

let pollTimer: number | undefined

function statusText(status: string): string {
  const map: Record<string, string> = { pending: '排队中', running: '运行中', done: '完成', failed: '失败' }
  return map[status] || status
}

async function syncData() {
  syncing.value = true
  try {
    const resp = (await request.post('/market/sync', { news: false })) as { task_id: string }
    syncTaskId.value = resp.task_id
  } finally {
    syncing.value = false
  }
  timer = window.setInterval(load, 3000)
}

onMounted(() => {
  load()
  // 后台任务表自动轮询（5 秒），同步/刷新进度实时可见
  pollTimer = window.setInterval(() => {
    request.get('/system/tasks').then((data) => {
      tasks.value = data as unknown as Array<Record<string, any>>
    }).catch(() => { /* 忽略轮询失败 */ })
  }, 5000)
})

onUnmounted(() => {
  window.clearInterval(timer)
  window.clearInterval(pollTimer)
})
</script>

<template>
  <el-row :gutter="12">
    <el-col :span="8">
      <el-card shadow="never">
        <template #header>系统配置</template>
        <el-descriptions :column="1" size="small">
          <el-descriptions-item label="自选池">
            <div>{{ config.watchlist?.join(', ') || '--' }}</div>
          </el-descriptions-item>
          <el-descriptions-item label="数据源">{{ config.data_source }}</el-descriptions-item>
          <el-descriptions-item label="大模型">{{ config.llm_model }}（{{ config.llm_mock ? '模拟模式' : '真实调用' }}）</el-descriptions-item>
          <el-descriptions-item label="API Key">{{ config.deepseek_api_key }}</el-descriptions-item>
          <el-descriptions-item label="AI 风控模式">{{ config.ai_risk_mode }}</el-descriptions-item>
          <el-descriptions-item label="数据库">{{ config.database_path }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card shadow="never" style="margin-top: 12px">
        <template #header>
          数据同步
          <span class="header-actions">
            <el-button size="small" type="primary" :loading="syncing" @click="syncData">
              同步自选池数据
            </el-button>
          </span>
        </template>
        <p class="tip">同步内容：交易日历、证券主表、自选池日线、指数（沪深300 等）。全市场同步请用命令行 scripts/sync_data.py --full（周末手动执行）。</p>
        <el-alert v-if="syncTaskId" type="info" :closable="false" title="同步任务已提交，下方任务列表可查看进度" />
      </el-card>
    </el-col>

    <el-col :span="16">
      <el-card shadow="never">
        <template #header>定时任务（调度器{{ scheduler.running ? '运行中' : '未启动' }}）</template>
        <el-table :data="scheduler.jobs" size="small" max-height="300">
          <el-table-column prop="id" label="任务" width="200" />
          <el-table-column prop="trigger" label="触发规则" />
          <el-table-column prop="next_run" label="下次执行" width="160" />
        </el-table>
      </el-card>

      <el-card shadow="never" style="margin-top: 12px">
        <template #header>后台任务</template>
        <el-table :data="tasks" size="small" max-height="260">
          <el-table-column prop="task_id" label="任务 ID" width="120" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.status === 'done' ? 'success' : row.status === 'failed' ? 'danger' : row.status === 'running' ? 'warning' : 'info'" size="small">
                {{ statusText(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="已用时" width="90">
            <template #default="{ row }">
              {{ row.elapsed !== undefined ? `${row.elapsed} 秒` : '--' }}
            </template>
          </el-table-column>
          <el-table-column prop="error" label="错误信息" />
        </el-table>
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
.tip {
  color: var(--app-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}
</style>
