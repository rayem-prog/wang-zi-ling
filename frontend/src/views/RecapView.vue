<template>
  <div class="view-container">
    <div class="view-toolbar">
      <div class="toolbar-left">
        <h2 class="view-title">📖 每日复盘简报阅读器</h2>
        <span class="view-subtitle">包含宏观温度计、命中率追踪、归因诊断与次日推演的完整 7 段式简报</span>
      </div>
      <div class="toolbar-right">
        <div class="date-selector">
          <label class="sel-label">选择日期：</label>
          <select v-model="selectedDate" class="terminal-select" @change="loadReport">
            <option v-for="d in dateList" :key="d" :value="d">{{ d }}</option>
          </select>
        </div>
        <button class="action-btn" @click="loadList" :disabled="loading">
          🔄 刷新列表
        </button>
      </div>
    </div>

    <!-- 报告渲染区域 -->
    <div class="report-wrapper">
      <div v-if="loading" class="loading-state">
        <span>正在读取复盘简报...</span>
      </div>
      <div v-else-if="!renderedHtml" class="empty-state">
        <span class="empty-icon">📄</span>
        <span>暂无复盘简报。收盘后 15:45 运行 daily_update.py 将自动产出报告。</span>
      </div>
      <div v-else class="markdown-body" v-html="renderedHtml"></div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { marked } from 'marked'
import { api } from '../api'

const loading = ref(false)
const dateList = ref([])
const selectedDate = ref('')
const reportContent = ref('')

const renderedHtml = computed(() => {
  if (!reportContent.value) return ''
  try {
    return marked.parse(reportContent.value)
  } catch (err) {
    return `<pre>${reportContent.value}</pre>`
  }
})

async function loadList() {
  loading.value = true
  try {
    const list = await api.getRecapList()
    dateList.value = list || []
    if (dateList.value.length > 0 && !selectedDate.value) {
      selectedDate.value = dateList.value[0]
      await loadReport()
    }
  } catch (err) {
    console.error('Failed to load recap list:', err)
  } finally {
    loading.value = false
  }
}

async function loadReport() {
  if (!selectedDate.value) return
  loading.value = true
  try {
    const res = await api.getRecapReport(selectedDate.value)
    reportContent.value = res.content || ''
  } catch (err) {
    reportContent.value = ''
    console.error('Failed to load report:', err)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadList()
})
</script>

<style scoped>
.view-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px 20px;
  gap: 16px;
  overflow-y: auto;
}

.view-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.view-title {
  font-size: 18px;
  font-weight: 700;
  color: #f0f6fc;
}

.view-subtitle {
  font-size: 12px;
  color: #8b949e;
  margin-top: 2px;
  display: block;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.date-selector {
  display: flex;
  align-items: center;
  gap: 6px;
}

.sel-label {
  font-size: 12px;
  color: #8b949e;
}

.terminal-select {
  background: #161b22;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
}

.action-btn {
  background: #21262d;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
}

.report-wrapper {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 24px 30px;
  flex: 1;
  overflow-y: auto;
}

.loading-state, .empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
  color: #8b949e;
  gap: 10px;
}

.empty-icon {
  font-size: 32px;
}

/* Markdown 深色金融样式 */
.markdown-body {
  color: #c9d1d9;
  font-size: 14px;
  line-height: 1.6;
}

:deep(.markdown-body h1) {
  font-size: 20px;
  color: #f0f6fc;
  border-bottom: 1px solid #30363d;
  padding-bottom: 8px;
  margin-bottom: 16px;
}

:deep(.markdown-body h2) {
  font-size: 16px;
  color: #58a6ff;
  margin-top: 20px;
  margin-bottom: 10px;
}

:deep(.markdown-body h3) {
  font-size: 14px;
  color: #e2e8f0;
  margin-top: 14px;
  margin-bottom: 8px;
}

:deep(.markdown-body table) {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0;
  font-size: 13px;
}

:deep(.markdown-body th), :deep(.markdown-body td) {
  border: 1px solid #30363d;
  padding: 8px 12px;
  text-align: left;
}

:deep(.markdown-body th) {
  background: #0d1117;
  color: #8b949e;
}

:deep(.markdown-body tr:nth-child(even)) {
  background: rgba(255, 255, 255, 0.02);
}

:deep(.markdown-body blockquote) {
  border-left: 3px solid #58a6ff;
  padding-left: 12px;
  color: #8b949e;
  margin: 12px 0;
}

:deep(.markdown-body ul) {
  padding-left: 20px;
  margin: 8px 0;
}
</style>
