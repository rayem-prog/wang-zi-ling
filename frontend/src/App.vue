<template>
  <div class="app-layout">
    <!-- 顶部状态栏 -->
    <TopHeader
      :status="systemStatus"
      :ws-connected="wsConnected"
      @refresh="refreshAll"
    />
    <div v-if="systemStatus?.data_conditions" class="data-warning" role="status">
      日线：{{ conditionLabel(systemStatus.data_conditions.daily) }}；
      分钟线：{{ conditionLabel(systemStatus.data_conditions.minute) }}；
      历史训练：{{ conditionLabel(systemStatus.data_conditions.training) }}。
      <span v-if="systemStatus.data_update">采集任务：{{ taskLabel(systemStatus.data_update.status) }} · {{ collectionStage(systemStatus.data_update.stage) }}。</span>
      <button @click="retryCollection" :disabled="collectionSubmitting || ['queued', 'running'].includes(systemStatus.data_update?.status)">
        {{ collectionSubmitting ? '正在提交…' : '补采缺口 / 重试采集' }}
      </button>
      <p v-if="collectionError || systemStatus.data_update?.error">{{ collectionError || systemStatus.data_update.error }}</p>
    </div>
    <div v-if="systemStatus?.data_health && !systemStatus.data_health.ready" class="data-warning" role="status">
      行情未通过检查：{{ systemStatus.data_health.reason }}。交易日 {{ systemStatus.data_health.trade_date || '未知' }}，
      覆盖 {{ systemStatus.data_health.covered_codes ?? '待核验' }}/{{ systemStatus.data_health.expected_codes ?? '待核验' }}；新推荐和指令已暂停。
    </div>
    <div v-if="systemStatus?.data_health?.excluded_codes?.length" class="data-warning" role="status">
      股票池中有已核实的停牌或退市证券，已排除出当前覆盖统计：
      {{ systemStatus.data_health.excluded_codes.map(item => `${item.code}（${item.status === 'delisted' ? '已退市' : '停牌'}）`).join('、') }}。
    </div>
    <div v-if="systemStatus?.data_health && (systemStatus.data_health.minute_status !== 'current' || systemStatus.data_health.minute_coverage < 0.98)" class="data-warning" role="status">
      分钟线{{ systemStatus.data_health.minute_status === 'empty' ? '尚未采集到真实数据' : `停留在 ${systemStatus.data_health.minute_latest_date || '未知'}` }}；
      覆盖 {{ systemStatus.data_health.minute_covered_codes ?? '待核验' }}/{{ systemStatus.data_health.minute_expected_codes ?? '待核验' }}。
      盘中细节可能不完整。日线来源 {{ systemStatus.data_health.source || '未知' }}，更新时间 {{ systemStatus.data_health.updated_at || '未知' }}。
    </div>

    <div class="data-warning" v-if="systemStatus?.data_health?.health_checked_at">
      健康检查 {{ systemStatus.data_health.health_checked_at }}
      {{ systemStatus.data_health?.health_cache_stale ? '（缓存已过期，暂停新买入）' : '' }}；
      行情更新时间 {{ systemStatus.data_health?.updated_at || '未知' }}；
      守护应用配置 {{ systemStatus.daemon?.config_version || '待确认' }}
    </div>
    <div class="app-main">
      <!-- 左侧边栏导航 -->
      <aside class="sidebar">
        <nav class="nav-menu">
          <button
            v-for="item in navItems"
            :key="item.key"
            class="nav-item"
            :class="{ active: activeTab === item.key }"
            @click="activeTab = item.key"
          >
            <span class="nav-icon">{{ item.icon }}</span>
            <span class="nav-text">{{ item.label }}</span>
            <span v-if="item.badge" class="nav-badge">{{ item.badge }}</span>
          </button>
        </nav>

        <div class="sidebar-footer">
          <div class="ws-indicator">
            <span class="dot" :class="wsConnected ? 'green' : 'gray'"></span>
            <span>{{ wsConnected ? '实时链路正常' : '轮询模式' }}</span>
          </div>
        </div>
      </aside>

      <!-- 主内容展示区 -->
      <main class="content-area">
        <component
          :is="currentViewComponent"
          :ref="el => { currentViewRef = el }"
          :system-status="systemStatus"
          @switch-tab="onSwitchTab"
        />
      </main>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import TopHeader from './components/TopHeader.vue'
import IntradayDecisionView from './views/IntradayDecisionView.vue'
import PaperTradingView from './views/PaperTradingView.vue'
import SignalsView from './views/SignalsView.vue'
import OrdersView from './views/OrdersView.vue'
import MacroView from './views/MacroView.vue'
import RecapView from './views/RecapView.vue'
import SettingsView from './views/SettingsView.vue'
import { api, createWebSocket } from './api'

const activeTab = ref('intraday')
const systemStatus = ref({})
const wsConnected = ref(false)
const currentViewRef = ref(null)
const collectionSubmitting = ref(false)
const collectionError = ref('')
let collectionFlight = null
function conditionLabel(value) {
  return ({ready:'通过',blocked:'未通过',checking:'核验中',unknown:'待核验'})[value?.state] || '待核验'
}
function taskLabel(value) {
  return ({queued:'排队',running:'执行中',succeeded:'采集完成',failed:'采集失败',interrupted:'已中断'})[value] || '尚未采集'
}
function collectionStage(value) {
  return ({fetch:'采集行情',fetch_daily:'日线补采',fetch_raw_daily:'不复权日线补采',fetch_spot:'报价采集',
    fetch_minute:'分钟线补采',audit:'核验条件',complete:'完成',queued:'排队'})[value] || value || '待执行'
}
function retryCollection() {
  if (collectionFlight) return collectionFlight
  if (['queued','running'].includes(systemStatus.value.data_update?.status)) return Promise.resolve()
  collectionSubmitting.value = true
  collectionError.value = ''
  collectionFlight = (async () => {
    try {
      const job = await api.retryMarketCollection()
      systemStatus.value = { ...systemStatus.value, data_update: job }
      await fetchStatus()
    } catch (err) {
      collectionError.value = err.response?.data?.detail || err.message || '采集任务提交失败，请重试'
    } finally {
      collectionSubmitting.value = false
      collectionFlight = null
    }
  })()
  return collectionFlight
}
let wsClient = null

const navItems = computed(() => [
  { key: 'intraday', icon: '🎯', label: '盘中决策', badge: '实时' },
  { key: 'paper', icon: '🎮', label: '模拟交易', badge: '100万' },
  { key: 'signals', icon: '⚡', label: '选股大厅', badge: systemStatus.value?.counts?.recommendations || null },
  { key: 'orders', icon: '📋', label: '操作指令', badge: systemStatus.value?.counts?.orders || null },
  { key: 'macro', icon: '🛡️', label: '宏观持仓', badge: null },
  { key: 'recap', icon: '📖', label: '每日复盘', badge: null },
  { key: 'settings', icon: '⚙️', label: '守护设置', badge: null }
])

const currentViewComponent = computed(() => {
  switch (activeTab.value) {
    case 'intraday': return IntradayDecisionView
    case 'paper': return PaperTradingView
    case 'signals': return SignalsView
    case 'orders': return OrdersView
    case 'macro': return MacroView
    case 'recap': return RecapView
    case 'settings': return SettingsView
    default: return IntradayDecisionView
  }
})

function onSwitchTab(payload) {
  if (typeof payload === 'string') {
    activeTab.value = payload
  } else if (payload && payload.tab) {
    activeTab.value = payload.tab
  }
}

async function fetchStatus() {
  try {
    const res = await api.getStatus()
    systemStatus.value = res || {}
  } catch (err) {
    console.debug('Failed to fetch status:', err)
  }
}

let refreshFlight = null
function refreshAll() {
  if (refreshFlight) return refreshFlight
  refreshFlight = Promise.allSettled([
    fetchStatus(),
    currentViewRef.value?.refresh?.()
  ]).finally(() => { refreshFlight = null })
  return refreshFlight
}

function handleWsMessage(payload) {
  if (payload.type === 'init' || payload.type === 'heartbeat') {
    if (payload.data) {
      systemStatus.value = payload.data
    }
  }
}

onMounted(() => {
  fetchStatus()
  wsClient = createWebSocket(
    handleWsMessage,
    (status) => {
      wsConnected.value = (status === 'connected')
    }
  )
})

onUnmounted(() => {
  if (wsClient) wsClient.close()
})
</script>

<style scoped>
.data-warning {
  padding: 9px 18px;
  color: #f0c674;
  background: #322718;
  border-bottom: 1px solid #705529;
  font-size: 13px;
}
.app-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100vw;
  background: #0d1117;
  overflow: hidden;
}

.app-main {
  display: flex;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.sidebar {
  width: 190px;
  background: #10141b;
  border-right: 1px solid #21262d;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 14px 10px;
  user-select: none;
  flex-shrink: 0;
  overflow-y: auto;
}

.nav-menu {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 8px;
  color: #8b949e;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  text-align: left;
}

.nav-item:hover {
  background: #161b22;
  color: #c9d1d9;
}

.nav-item.active {
  background: rgba(56, 139, 253, 0.15);
  color: #58a6ff;
  border-color: rgba(56, 139, 253, 0.3);
  font-weight: 600;
}

.nav-icon {
  font-size: 16px;
}

.nav-text {
  flex: 1;
}

.nav-badge {
  background: #21262d;
  color: #c9d1d9;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 10px;
}

.sidebar-footer {
  padding-top: 12px;
  border-top: 1px solid #21262d;
}

.ws-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: #8b949e;
  padding: 4px 6px;
}

.ws-indicator .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.ws-indicator .dot.green {
  background: #3fb950;
}

.ws-indicator .dot.gray {
  background: #8b949e;
}

.content-area {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: #0d1117;
  overflow-y: auto;
  overflow-x: hidden;
  position: relative;
}

/* 全局自定义滚动条 */
::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}
::-webkit-scrollbar-track {
  background: #0d1117;
}
::-webkit-scrollbar-thumb {
  background: #30363d;
  border: 2px solid #0d1117;
  border-radius: 6px;
}
::-webkit-scrollbar-thumb:hover {
  background: #388bfd;
}
</style>
