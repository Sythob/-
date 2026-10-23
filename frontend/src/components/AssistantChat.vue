<script setup lang="ts">
import { marked } from 'marked'
import { nextTick, onMounted, ref } from 'vue'

import request from '../api/request'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  model?: string
}

const open = ref(false)
const messages = ref<ChatMessage[]>([])
const input = ref('')
const loading = ref(false)
const listEl = ref<HTMLElement>()

// ---- 面板拖动 ----
const PANEL_W = 380
const PANEL_H = 540
const panelPos = ref({ x: 0, y: 0 })
let dragging = false
let dragStart = { mx: 0, my: 0, px: 0, py: 0 }

function initPanelPos() {
  panelPos.value = {
    x: Math.max(8, window.innerWidth - PANEL_W - 20),
    y: Math.max(8, window.innerHeight - PANEL_H - 20),
  }
}

function onDragStart(e: MouseEvent) {
  dragging = true
  dragStart = { mx: e.clientX, my: e.clientY, px: panelPos.value.x, py: panelPos.value.y }
  window.addEventListener('mousemove', onDragMove)
  window.addEventListener('mouseup', onDragEnd)
}

function onDragMove(e: MouseEvent) {
  if (!dragging) return
  panelPos.value = {
    x: Math.min(Math.max(dragStart.px + e.clientX - dragStart.mx, 8),
      window.innerWidth - PANEL_W - 8),
    y: Math.min(Math.max(dragStart.py + e.clientY - dragStart.my, 8),
      window.innerHeight - PANEL_H - 8),
  }
}

function onDragEnd() {
  dragging = false
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', onDragEnd)
}

const STORAGE_KEY = 'qstock_assistant_history'

onMounted(() => {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) messages.value = JSON.parse(saved).slice(-20)
  } catch { messages.value = [] }
})

function saveHistory() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.value.slice(-20)))
  } catch { /* 存储失败忽略 */ }
}

async function scrollToBottom() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

async function send() {
  const text = input.value.trim()
  if (!text || loading.value) return
  input.value = ''
  messages.value.push({ role: 'user', content: text })
  loading.value = true
  await scrollToBottom()
  try {
    const resp = (await request.post('/ai/chat', {
      message: text,
      history: messages.value.slice(-10),
    }, { timeout: 120000 })) as { reply: string; model: string }
    messages.value.push({ role: 'assistant', content: resp.reply, model: resp.model })
  } catch {
    messages.value.push({ role: 'assistant', content: '抱歉，AI 助手暂时不可用，请稍后重试。' })
  } finally {
    loading.value = false
    saveHistory()
    await scrollToBottom()
  }
}

function render(content: string): string {
  return marked.parse(content, { async: false }) as string
}
</script>

<template>
  <div class="assistant-root">
    <el-button
      v-if="!open"
      class="assistant-fab"
      type="primary"
      circle
      @click="open = true; initPanelPos(); scrollToBottom()"
    >
      AI
    </el-button>

    <div v-else class="assistant-panel" :style="{ left: panelPos.x + 'px', top: panelPos.y + 'px' }">
      <div class="assistant-header" @mousedown="onDragStart">
        <span>🤖 AI 小助手（按住此处可拖动）</span>
        <el-button link size="small" @mousedown.stop @click="open = false">收起</el-button>
      </div>
      <div ref="listEl" class="assistant-messages">
        <div v-if="!messages.length" class="assistant-hint">
          问我任何问题：<br />「今天市场怎么样」「解释一下 MACD」「我的持仓风险大吗」「怎么做波段」
        </div>
        <div
          v-for="(m, i) in messages"
          :key="i"
          class="assistant-msg"
          :class="m.role === 'user' ? 'msg-user' : 'msg-assistant'"
        >
          <div class="msg-bubble" v-html="render(m.content)" />
          <div v-if="m.model" class="msg-model">{{ m.model }}</div>
        </div>
        <div v-if="loading" class="assistant-msg msg-assistant">
          <div class="msg-bubble msg-loading">思考中...</div>
        </div>
      </div>
      <div class="assistant-input">
        <el-input
          v-model="input"
          placeholder="输入问题，回车发送"
          size="small"
          :disabled="loading"
          @keyup.enter="send"
        />
        <el-button type="primary" size="small" :loading="loading" @click="send">发送</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.assistant-root {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 2000;
}

.assistant-fab {
  width: 52px;
  height: 52px;
  font-size: 14px;
  font-weight: 700;
  box-shadow: 0 2px 12px rgba(47, 111, 237, 0.4);
}

.assistant-panel {
  position: fixed;
  width: 380px;
  height: 540px;
  background: var(--app-card);
  border-radius: 10px;
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.assistant-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  background: #2f6fed;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: move;
  user-select: none;
}

.assistant-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  background: var(--app-bg);
}

.assistant-hint {
  color: var(--app-text-secondary);
  font-size: 13px;
  line-height: 1.8;
  padding: 20px 8px;
}

.assistant-msg {
  margin-bottom: 10px;
  display: flex;
  flex-direction: column;
}

.msg-user {
  align-items: flex-end;
}

.msg-assistant {
  align-items: flex-start;
}

.msg-bubble {
  max-width: 86%;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.7;
  word-break: break-word;
}

.msg-user .msg-bubble {
  background: #2f6fed;
  color: #fff;
}

.msg-assistant .msg-bubble {
  background: var(--app-card);
  border: 1px solid var(--app-border);
}

.msg-loading {
  color: var(--app-text-secondary);
}

.msg-model {
  font-size: 11px;
  color: var(--app-text-secondary);
  margin-top: 2px;
  padding: 0 4px;
}

.assistant-input {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid var(--app-border);
  background: var(--app-card);
}
</style>
