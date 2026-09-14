<template>
  <div class="view-container">
    <div class="view-toolbar">
      <div class="toolbar-left">
        <h2 class="view-title">📋 次日操作指令单</h2>
        <span class="view-subtitle">由收盘后策略引擎标准化生成，盘中按纪律挂单执行</span>
      </div>
      <div class="toolbar-right">
        <a :href="exportUrl" download class="action-btn download-btn">
          ⬇️ 导出 CSV 指令单
        </a>
        <button class="action-btn" @click="loadOrders" :disabled="loading">
          {{ loading ? '加载中...' : '🔄 刷新指令' }}
        </button>
      </div>
    </div>

    <!-- 风险提示横幅 -->
    <div class="risk-banner">
      <span class="banner-icon">⚠️</span>
      <div class="banner-text">
        <b>交易纪律守则：</b>
        <span>开盘若跳空高开 ≥ 9.8% 涨停则严禁追高，自动放弃买入；价格回踩进入指定区间后手动下单；硬止损 -5%，目标止盈 +15%。</span>
      </div>
    </div>

    <!-- 指令单表格 -->
    <div class="table-wrapper">
      <table class="terminal-table">
        <thead>
          <tr>
            <th width="60">方向</th>
            <th width="100">代码</th>
            <th width="120">名称</th>
            <th width="100">建议股数</th>
            <th width="140">买入/卖出区间</th>
            <th width="110">硬止损 (-5%)</th>
            <th width="110">目标止盈 (+15%)</th>
            <th>风控提示与说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="orders.length === 0">
            <td colspan="8" class="empty-cell">
              {{ loading ? '正在读取指令单...' : '当前暂无生效中的指令单。收盘后 15:45 运行 daily_update.py 将自动产出。' }}
            </td>
          </tr>
          <tr v-for="(ord, idx) in orders" :key="idx">
            <td>
              <span
                class="side-tag"
                :class="ord.side === '买入' ? 'buy' : 'sell'"
              >
                {{ ord.side || '买入' }}
              </span>
            </td>
            <td class="code-col"><b>{{ ord.code }}</b></td>
            <td>{{ ord.name || '--' }}</td>
            <td class="num-col">{{ ord.shares ? Number(ord.shares).toLocaleString() : '--' }}</td>
            <td class="range-col">
              <span v-if="ord.price_low && ord.price_high">
                ¥{{ Number(ord.price_low).toFixed(2) }} ~ ¥{{ Number(ord.price_high).toFixed(2) }}
              </span>
              <span v-else-if="ord.target_price">¥{{ Number(ord.target_price).toFixed(2) }}</span>
              <span v-else>--</span>
            </td>
            <td class="stop-loss">
              {{ ord.stop_loss ? '¥' + Number(ord.stop_loss).toFixed(2) : '--' }}
            </td>
            <td class="take-profit">
              {{ ord.take_profit ? '¥' + Number(ord.take_profit).toFixed(2) : '--' }}
            </td>
            <td class="note-col">
              <span class="warning-pill">{{ ord.note || '跳空≥9.8%涨停不追' }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'

const orders = ref([])
const loading = ref(false)
const exportUrl = api.getOrdersExportUrl()

async function loadOrders() {
  loading.value = true
  try {
    const res = await api.getOrders()
    orders.value = res || []
  } catch (err) {
    console.error('Failed to load orders:', err)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadOrders()
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
  gap: 10px;
}

.action-btn {
  background: #21262d;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  transition: all 0.2s;
}

.action-btn:hover {
  background: #30363d;
  color: #f0f6fc;
}

.download-btn {
  background: rgba(56, 139, 253, 0.15);
  color: #58a6ff;
  border-color: rgba(56, 139, 253, 0.4);
}

.download-btn:hover {
  background: rgba(56, 139, 253, 0.25);
  color: #79c0ff;
}

.risk-banner {
  background: rgba(210, 153, 34, 0.1);
  border: 1px solid rgba(210, 153, 34, 0.3);
  border-radius: 8px;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.banner-icon {
  font-size: 18px;
}

.banner-text {
  color: #d29922;
  line-height: 1.4;
}

.table-wrapper {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  overflow: hidden;
  flex: 1;
}

.terminal-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
  font-size: 13px;
}

.terminal-table th {
  background: #0d1117;
  color: #8b949e;
  font-weight: 600;
  padding: 10px 14px;
  border-bottom: 1px solid #30363d;
  font-size: 12px;
}

.terminal-table td {
  padding: 10px 14px;
  border-bottom: 1px solid #21262d;
  color: #c9d1d9;
}

.side-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
}

.side-tag.buy {
  background: rgba(239, 68, 68, 0.15);
  color: #ef4444;
  border: 1px solid rgba(239, 68, 68, 0.3);
}

.side-tag.sell {
  background: rgba(16, 185, 129, 0.15);
  color: #10b981;
  border: 1px solid rgba(16, 185, 129, 0.3);
}

.code-col {
  color: #58a6ff;
  font-family: monospace;
}

.num-col {
  font-family: monospace;
  font-weight: 600;
}

.range-col {
  font-family: monospace;
  color: #f0f6fc;
}

.stop-loss {
  color: #10b981;
  font-weight: 600;
  font-family: monospace;
}

.take-profit {
  color: #ef4444;
  font-weight: 600;
  font-family: monospace;
}

.warning-pill {
  display: inline-block;
  font-size: 11px;
  background: #21262d;
  color: #8b949e;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid #30363d;
}

.empty-cell {
  text-align: center;
  padding: 40px;
  color: #8b949e;
}
</style>
