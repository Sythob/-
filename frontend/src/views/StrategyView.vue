<script setup lang="ts">
import { onMounted, ref } from 'vue'

import request from '../api/request'

interface StrategyMeta {
  name: string
  display_name: string
  description: string
  interval: string
  params: Record<string, any>
  params_schema: Array<{ name: string; label: string; type: string; default: number; min?: number; max?: number }>
}

const strategies = ref<StrategyMeta[]>([])
const generated = ref<{ id: number; name: string; code: string; created_at: string }[]>([])
const description = ref('')
const generating = ref(false)
const generatedCode = ref('')
const dialogVisible = ref(false)

async function load() {
  strategies.value = (await request.get('/strategies')) as StrategyMeta[]
  generated.value = (await request.get('/strategies/generated')) as typeof generated.value
}

async function generate() {
  if (!description.value.trim()) return
  generating.value = true
  try {
    const resp = (await request.post('/strategies/generate', {
      description: description.value.trim(),
    })) as { id: number; name: string; code: string }
    generatedCode.value = resp.code
    dialogVisible.value = true
    await load()
  } finally {
    generating.value = false
  }
}

onMounted(load)
</script>

<template>
  <el-row :gutter="12">
    <el-col :span="14">
      <el-card shadow="never">
        <template #header>内置策略</template>
        <div v-for="s in strategies" :key="s.name" class="strategy-item">
          <div class="strategy-head">
            <span class="strategy-name">{{ s.display_name }}</span>
            <el-tag size="small" type="info">{{ s.interval === 'daily' ? '日线' : s.interval }}</el-tag>
            <span class="strategy-code">（{{ s.name }}）</span>
          </div>
          <p class="strategy-desc">{{ s.description }}</p>
          <el-descriptions v-if="s.params_schema?.length" :column="2" size="small">
            <el-descriptions-item v-for="p in s.params_schema" :key="p.name" :label="p.label || p.name">
              {{ s.params[p.name] }}（{{ p.type === 'int' ? '整数' : '小数' }}{{ p.min !== undefined ? `，范围 ${p.min}~${p.max}` : '' }}）
            </el-descriptions-item>
          </el-descriptions>
        </div>
      </el-card>

      <el-card shadow="never" style="margin-top: 12px">
        <template #header>AI 生成的策略</template>
        <el-table v-if="generated.length" :data="generated" size="small">
          <el-table-column prop="name" label="名称" width="180" />
          <el-table-column prop="created_at" label="创建时间" />
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="generatedCode = row.code; dialogVisible = true">
                查看代码
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="暂无生成策略，用右侧自然语言描述来生成第一个" :image-size="60" />
      </el-card>
    </el-col>

    <el-col :span="10">
      <el-card shadow="never">
        <template #header>自然语言生成策略</template>
        <el-input
          v-model="description"
          type="textarea"
          :rows="6"
          placeholder="例：当 5 日均线上穿 20 日均线时买入，下穿时卖出，持仓期间跌破 10 日均线止损"
        />
        <div class="gen-tips">
          生成流程：大模型写代码 → import 白名单与语法校验 → 用样本数据空跑 → 入库。
          生成的策略仅用于回测与模拟盘，不接触真实资金。
        </div>
        <el-button type="primary" :loading="generating" style="width: 100%" @click="generate">
          生成策略
        </el-button>
      </el-card>
    </el-col>
  </el-row>

  <el-dialog v-model="dialogVisible" title="策略代码" width="640px">
    <pre class="code-block">{{ generatedCode }}</pre>
    <template #footer>
      <el-button @click="dialogVisible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.strategy-item {
  padding: 12px 0;
  border-bottom: 1px solid #f0f2f5;
}

.strategy-item:last-child {
  border-bottom: none;
}

.strategy-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.strategy-name {
  font-weight: 600;
  font-size: 15px;
}

.strategy-code {
  color: var(--app-text-secondary);
  font-size: 12px;
}

.strategy-desc {
  color: #606266;
  font-size: 13px;
  margin: 6px 0;
}

.gen-tips {
  color: var(--app-text-secondary);
  font-size: 12px;
  line-height: 1.6;
  margin: 8px 0 12px;
}

.code-block {
  background: #f6f8fa;
  border-radius: 6px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.5;
  overflow: auto;
  max-height: 420px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
