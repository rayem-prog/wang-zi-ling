<template>
  <div class="view-container">
    <!-- 顶部工具栏 -->
    <div class="view-toolbar">
      <div class="toolbar-left">
        <h2 class="view-title">📖 每日复盘与算法自适应演化</h2>
        <span class="view-subtitle">全市场行情复盘 · 双引擎超额收益评估 · 动态权重微调 · 多因子公式与模型参数自适应优化</span>
      </div>
      <div class="toolbar-right">
        <div class="date-selector">
          <label class="sel-label">选择日期：</label>
          <select v-model="selectedDate" class="terminal-select" @change="loadReport" :disabled="loading || runningRecap">
            <option v-for="d in dateList" :key="d" :value="d">{{ d }}</option>
          </select>
        </div>
        <button class="action-btn" @click="handleRefresh" :disabled="loading || runningRecap">
          🔄 刷新
        </button>
        <button 
          class="action-btn run-recap-btn" 
          :class="{ 'is-running': runningRecap }" 
          @click="handleRunRecap(true)" 
          :disabled="runningRecap"
        >
          <span v-if="runningRecap" class="spinner-inline"></span>
          <span>{{ runningRecap ? '正在计算与优化中...' : '⚡ 立即执行今日复盘与算法优化' }}</span>
        </button>
      </div>
    </div>

    <!-- 运行状态提示条 -->
    <div v-if="runningRecap || runNotice" class="status-banner" :class="{ 'banner-running': runningRecap, 'banner-success': !runningRecap && runSuccess, 'banner-error': !runningRecap && !runSuccess }">
      <span class="banner-icon">{{ runningRecap ? '⏳' : (runSuccess ? '✅' : '⚠️') }}</span>
      <span class="banner-text">{{ runNotice }}</span>
    </div>

    <!-- 核心指标摘要看板 -->
    <div class="summary-cards">
      <div class="summary-card">
        <span class="sc-label">⚖️ 双引擎当前融合权重 (A : B)</span>
        <div class="sc-val">
          <span class="val-engine-a">A {{ formatPct(summary?.current_weights?.weight_a) }}</span>
          <span class="val-sep">:</span>
          <span class="val-engine-b">B {{ formatPct(summary?.current_weights?.weight_b) }}</span>
        </div>
        <span class="sc-desc">LightGBM机器学习 vs 宏观多因子规则</span>
      </div>

      <div class="summary-card">
        <span class="sc-label">🧠 算法与公式调优机制</span>
        <div class="sc-val status-badge-val">
          <span class="status-tag active">动态自适应演化</span>
        </div>
        <span class="sc-desc">根据当日真实超额收益惩罚劣势、奖励优势</span>
      </div>

      <div class="summary-card">
        <span class="sc-label">📅 最新复盘归档日期</span>
        <div class="sc-val code-font">{{ summary?.latest_date || '未生成' }}</div>
        <span class="sc-desc">收盘后自动执行全市场行情与超额评估</span>
      </div>

      <div class="summary-card">
        <span class="sc-label">📚 已沉淀复盘简报</span>
        <div class="sc-val code-font">{{ summary?.total_reports || 0 }} <span class="unit">期</span></div>
        <span class="sc-desc">完整 7 段式量化归因与次日演练</span>
      </div>
    </div>

    <!-- 报告渲染区域 -->
    <div class="report-wrapper">
      <div v-if="loading" class="loading-state">
        <div class="spinner-large"></div>
        <span>正在读取复盘简报数据...</span>
      </div>
      <div v-else-if="!renderedHtml" class="empty-state">
        <span class="empty-icon">📄</span>
        <div class="empty-title">暂无所选日期的复盘简报</div>
        <div class="empty-desc">
          系统支持每日收盘后一键执行复盘：自动拉取全市场当日行情、评估双引擎胜率与超额、动态更新权重，并优化多因子公式。
        </div>
        <button 
          class="action-btn run-recap-btn empty-btn" 
          :class="{ 'is-running': runningRecap }" 
          @click="handleRunRecap(true)" 
          :disabled="runningRecap"
        >
          <span v-if="runningRecap" class="spinner-inline"></span>
          <span>{{ runningRecap ? '正在计算中...' : '⚡ 立即执行今日复盘与算法优化' }}</span>
        </button>
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
const runningRecap = ref(false)
const runNotice = ref('')
const runSuccess = ref(true)

const dateList = ref([])
const selectedDate = ref('')
const reportContent = ref('')
const summary = ref(null)

const renderedHtml = computed(() => {
  if (!reportContent.value) return ''
  try {
    return marked.parse(reportContent.value)
  } catch (err) {
    return `<pre>${reportContent.value}</pre>`
  }
})

function formatPct(val) {
  if (val === undefined || val === null || isNaN(val)) return '--%'
  return (val * 100).toFixed(1) + '%'
}

async function loadSummary() {
  try {
    const res = await api.getRecapSummary()
    summary.value = res
  } catch (err) {
    console.error('Failed to load recap summary:', err)
  }
}

async function loadList() {
  loading.value = true
  try {
    const list = await api.getRecapList()
    dateList.value = list || []
    if (dateList.value.length > 0 && (!selectedDate.value || !dateList.value.includes(selectedDate.value))) {
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
  if (!selectedDate.value) {
    reportContent.value = ''
    return
  }
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

async function handleRefresh() {
  await Promise.all([loadList(), loadSummary()])
}

async function handleRunRecap(fastMode = true) {
  if (runningRecap.value) return
  runningRecap.value = true
  runNotice.value = '正在拉取全市场数据、评估双引擎胜率与超额、自适应调权并优化公式算法...'
  runSuccess.value = true

  try {
    const res = await api.runDailyRecap(fastMode)
    runSuccess.value = true
    const updatedDate = res?.date || selectedDate.value
    runNotice.value = `今日复盘与算法调优完成！已生成 ${updatedDate} 简报，双引擎权重与多因子公式已自适应更新。`

    // 重新获取列表与摘要
    await loadSummary()
    const list = await api.getRecapList()
    dateList.value = list || []
    if (updatedDate) {
      selectedDate.value = updatedDate
    } else if (dateList.value.length > 0) {
      selectedDate.value = dateList.value[0]
    }
    await loadReport()

    setTimeout(() => {
      if (!runningRecap.value) {
        runNotice.value = ''
      }
    }, 6000)
  } catch (err) {
    runSuccess.value = false
    runNotice.value = `执行复盘失败: ${err?.response?.data?.detail || err?.message || '未知错误'}`
  } finally {
    runningRecap.value = false
  }
}

onMounted(() => {
  handleRefresh()
})
</script>

<style scoped>
.view-container {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  padding: 16px 20px 48px 20px;
  gap: 16px;
}

.view-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.view-title {
  font-size: 18px;
  font-weight: 700;
  color: #f0f6fc;
}

.view-subtitle {
  font-size: 12px;
  color: #8b949e;
  margin-top: 3px;
  display: block;
}

.toolbar-right {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
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
  padding: 6px 12px;
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
  font-weight: 500;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: all .2s;
}

.action-btn:hover:not(:disabled) {
  background: #30363d;
  color: #f0f6fc;
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.run-recap-btn {
  background: linear-gradient(135deg, #1f6feb 0%, #238636 100%);
  border: 1px solid #388bfd;
  color: #ffffff;
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(31, 111, 235, 0.25);
}

.run-recap-btn:hover:not(:disabled) {
  background: linear-gradient(135deg, #388bfd 0%, #2ea043 100%);
  box-shadow: 0 4px 12px rgba(31, 111, 235, 0.4);
}

.run-recap-btn.is-running {
  background: #21262d;
  border-color: #30363d;
  color: #8b949e;
}

.spinner-inline {
  width: 13px;
  height: 13px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #ffffff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.spinner-large {
  width: 32px;
  height: 32px;
  border: 3px solid #30363d;
  border-top-color: #58a6ff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 运行状态通知条 */
.status-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.4;
  transition: all .3s;
}

.banner-running {
  background: rgba(56, 139, 253, 0.12);
  border: 1px solid rgba(56, 139, 253, 0.35);
  color: #79c0ff;
}

.banner-success {
  background: rgba(46, 160, 67, 0.12);
  border: 1px solid rgba(46, 160, 67, 0.35);
  color: #3fb950;
}

.banner-error {
  background: rgba(248, 81, 73, 0.12);
  border: 1px solid rgba(248, 81, 73, 0.35);
  color: #f85149;
}

/* 指标卡片 */
.summary-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

@media (max-width: 1024px) {
  .summary-cards {
    grid-template-columns: repeat(2, 1fr);
  }
}

.summary-card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.sc-label {
  font-size: 12px;
  color: #8b949e;
  font-weight: 500;
}

.sc-val {
  font-size: 20px;
  font-weight: 700;
  color: #f0f6fc;
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.val-engine-a {
  color: #58a6ff;
  font-family: monospace;
}

.val-engine-b {
  color: #3fb950;
  font-family: monospace;
}

.val-sep {
  color: #8b949e;
  font-size: 14px;
}

.status-badge-val {
  font-size: 14px;
}

.status-tag {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
}

.status-tag.active {
  background: rgba(35, 134, 54, 0.2);
  color: #3fb950;
  border: 1px solid rgba(63, 185, 80, 0.3);
}

.code-font {
  font-family: monospace;
}

.unit {
  font-size: 13px;
  font-weight: normal;
  color: #8b949e;
}

.sc-desc {
  font-size: 11px;
  color: #8b949e;
}

/* 报告容器 */
.report-wrapper {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 24px 32px;
  min-height: 400px;
}

.loading-state, .empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  color: #8b949e;
  gap: 14px;
  text-align: center;
}

.empty-icon {
  font-size: 40px;
}

.empty-title {
  font-size: 16px;
  font-weight: 600;
  color: #f0f6fc;
}

.empty-desc {
  max-width: 520px;
  font-size: 13px;
  color: #8b949e;
  line-height: 1.6;
}

.empty-btn {
  margin-top: 10px;
  padding: 9px 20px;
  font-size: 14px;
}

/* Markdown 深色金融样式 */
.markdown-body {
  color: #c9d1d9;
  font-size: 14px;
  line-height: 1.7;
}

:deep(.markdown-body h1) {
  font-size: 22px;
  color: #f0f6fc;
  border-bottom: 1px solid #30363d;
  padding-bottom: 10px;
  margin-bottom: 20px;
}

:deep(.markdown-body h2) {
  font-size: 17px;
  color: #58a6ff;
  border-bottom: 1px solid #21262d;
  padding-bottom: 6px;
  margin-top: 24px;
  margin-bottom: 12px;
}

:deep(.markdown-body h3) {
  font-size: 15px;
  color: #e2e8f0;
  margin-top: 16px;
  margin-bottom: 8px;
}

:deep(.markdown-body table) {
  width: 100%;
  border-collapse: collapse;
  margin: 14px 0;
  font-size: 13px;
}

:deep(.markdown-body th), :deep(.markdown-body td) {
  border: 1px solid #30363d;
  padding: 9px 14px;
  text-align: left;
}

:deep(.markdown-body th) {
  background: #0d1117;
  color: #8b949e;
  font-weight: 600;
}

:deep(.markdown-body tr:nth-child(even)) {
  background: rgba(255, 255, 255, 0.02);
}

:deep(.markdown-body blockquote) {
  border-left: 3px solid #58a6ff;
  padding: 6px 14px;
  background: rgba(56, 139, 253, 0.06);
  color: #8b949e;
  margin: 14px 0;
  border-radius: 0 4px 4px 0;
}

:deep(.markdown-body ul) {
  padding-left: 20px;
  margin: 10px 0;
}

:deep(.markdown-body li) {
  margin: 4px 0;
}

:deep(.markdown-body code) {
  background: #21262d;
  color: #79c0ff;
  padding: 2px 6px;
  border-radius: 4px;
  font-family: monospace;
  font-size: 12px;
}

:deep(.markdown-body strong) {
  color: #f0f6fc;
}
</style>
