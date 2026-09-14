<template>
  <div class="app-layout">
    <!-- 顶部状态栏 -->
    <TopHeader
      :status="systemStatus"
      :ws-connected="wsConnected"
      @refresh="refreshAll"
    />

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

function refreshAll() {
  fetchStatus()
  if (currentViewRef.value) {
    if (typeof currentViewRef.value.loadSignals === 'function') currentViewRef.value.loadSignals()
    if (typeof currentViewRef.value.loadOrders === 'function') currentViewRef.value.loadOrders()
    if (typeof currentViewRef.value.loadAll === 'function') currentViewRef.value.loadAll()
  }
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
  background: #0d1117;
  overflow: hidden;
}
</style>
