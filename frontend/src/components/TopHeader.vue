<template>
  <header class="top-header">
    <div class="brand">
      <span class="logo-icon">📈</span>
      <div class="brand-text">
        <span class="brand-title">StockPilot</span>
        <span class="brand-ver">v2.0 Terminal</span>
      </div>
    </div>

    <div class="status-items">
      <!-- 实时时钟 -->
      <div class="status-pill clock">
        <span class="pill-dot blink"></span>
        <span class="pill-label">{{ currentTime }}</span>
      </div>

      <!-- 市场交易时段 -->
      <div class="status-pill market" :class="marketClass">
        <span class="pill-dot"></span>
        <span class="pill-label">{{ marketLabel }}</span>
      </div>

      <!-- 宏观档位 -->
      <div class="status-pill macro" :class="macroClass">
        <span class="pill-icon">{{ macroIcon }}</span>
        <span class="pill-label">{{ macroText }}</span>
      </div>

      <!-- 守护心跳 -->
      <div class="status-pill daemon" :class="daemonClass">
        <span class="pill-dot"></span>
        <span class="pill-label">{{ daemonText }}</span>
      </div>

      <!-- 刷新与网络 -->
      <button class="refresh-btn" @click="$emit('refresh')" title="立即同步全量数据">
        🔄 刷新
      </button>
    </div>
  </header>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'

const props = defineProps({
  status: {
    type: Object,
    default: () => ({})
  },
  wsConnected: {
    type: Boolean,
    default: false
  }
})

defineEmits(['refresh'])

const currentTime = ref('')
let timer = null

function updateTime() {
  const now = new Date()
  currentTime.value = now.toTimeString().split(' ')[0]
}

onMounted(() => {
  updateTime()
  timer = setInterval(updateTime, 1000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

const marketLabel = computed(() => {
  return props.status?.market?.label || '检测中...'
})

const marketClass = computed(() => {
  const isTrading = props.status?.market?.is_trading
  const phase = props.status?.market?.phase
  if (isTrading) return 'trading'
  if (phase === 'lunch_break' || phase === 'call_auction') return 'warning'
  return 'closed'
})

const macroStance = computed(() => {
  return (props.status?.macro?.stance || 'NEUTRAL').toUpperCase()
})

const macroIcon = computed(() => {
  if (macroStance.value === 'ATTACK') return '🚀'
  if (macroStance.value === 'DEFENSE') return '🛡️'
  return '⚖️'
})

const macroText = computed(() => {
  const cap = Math.round((props.status?.macro?.cap || 0.6) * 100)
  if (macroStance.value === 'ATTACK') return `进攻档 (${cap}%仓)`
  if (macroStance.value === 'DEFENSE') return `防守档 (${cap}%仓·禁开)`
  return `中性档 (${cap}%仓)`
})

const macroClass = computed(() => {
  if (macroStance.value === 'ATTACK') return 'attack'
  if (macroStance.value === 'DEFENSE') return 'defense'
  return 'neutral'
})

const daemonOnline = computed(() => {
  return !!props.status?.daemon?.online
})

const daemonText = computed(() => {
  if (daemonOnline.value) {
    const pid = props.status?.daemon?.pid
    return `守护在线 (PID:${pid})`
  }
  return '守护离线'
})

const daemonClass = computed(() => {
  return daemonOnline.value ? 'online' : 'offline'
})
</script>

<style scoped>
.top-header {
  height: 54px;
  background: #0d1117;
  border-bottom: 1px solid #21262d;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 18px;
  user-select: none;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
}

.logo-icon {
  font-size: 22px;
}

.brand-text {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.brand-title {
  font-size: 16px;
  font-weight: 700;
  color: #f0f6fc;
  letter-spacing: 0.5px;
}

.brand-ver {
  font-size: 11px;
  color: #8b949e;
  background: #161b22;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid #30363d;
}

.status-items {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid transparent;
}

.pill-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
}

.blink {
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.clock {
  background: #161b22;
  color: #c9d1d9;
  border-color: #30363d;
  font-family: monospace;
  font-size: 13px;
}

.market.trading {
  background: rgba(35, 134, 54, 0.15);
  color: #3fb950;
  border-color: rgba(63, 185, 80, 0.3);
}

.market.warning {
  background: rgba(210, 153, 34, 0.15);
  color: #d29922;
  border-color: rgba(210, 153, 34, 0.3);
}

.market.closed {
  background: #161b22;
  color: #8b949e;
  border-color: #30363d;
}

.macro.attack {
  background: rgba(46, 160, 67, 0.15);
  color: #3fb950;
  border-color: rgba(63, 185, 80, 0.3);
}

.macro.neutral {
  background: rgba(56, 139, 253, 0.15);
  color: #58a6ff;
  border-color: rgba(88, 166, 255, 0.3);
}

.macro.defense {
  background: rgba(248, 81, 73, 0.15);
  color: #f85149;
  border-color: rgba(248, 81, 73, 0.3);
}

.daemon.online {
  background: rgba(35, 134, 54, 0.15);
  color: #3fb950;
  border-color: rgba(63, 185, 80, 0.3);
}

.daemon.offline {
  background: rgba(248, 81, 73, 0.15);
  color: #f85149;
  border-color: rgba(248, 81, 73, 0.3);
}

.refresh-btn {
  background: #21262d;
  color: #c9d1d9;
  border: 1px solid #30363d;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.refresh-btn:hover {
  background: #30363d;
  color: #f0f6fc;
}
</style>
