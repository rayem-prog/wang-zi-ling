<template>
  <div class="view-container">
    <!-- 顶部状态与统计卡片 -->
    <div class="view-toolbar">
      <div class="toolbar-left">
        <h2 class="view-title">🎯 盘中实时决策与异动雷达</h2>
        <span class="view-subtitle">秒级盯盘诊断：-5% 硬止损警报、+15% 目标止盈指令、建仓到位追踪及盘中放量雷达</span>
      </div>
      <div class="toolbar-right">
        <button class="action-btn" @click="loadData" :disabled="loading">
          {{ loading ? '计算中...' : '🔄 立即刷新决策' }}
        </button>
      </div>
    </div>

    <!-- 决策统计横幅 -->
    <div class="summary-cards">
      <div class="summary-card" :class="{ alert: (decisions.summary?.urgent_count || 0) > 0 }">
        <span class="sc-label">触发硬止损 (≤ -5%)</span>
        <span class="sc-val stop">{{ decisions.summary?.urgent_count || 0 }} 只</span>
        <span class="sc-desc">{{ (decisions.summary?.urgent_count || 0) > 0 ? '⚠️ 必须坚决执行止损！' : '暂无持仓触碰硬止损' }}</span>
      </div>

      <div class="summary-card" :class="{ profit: (decisions.summary?.take_profit_count || 0) > 0 }">
        <span class="sc-label">达标目标止盈 (≥ +15%)</span>
        <span class="sc-val take">{{ decisions.summary?.take_profit_count || 0 }} 只</span>
        <span class="sc-desc">{{ (decisions.summary?.take_profit_count || 0) > 0 ? '🎉 建议兑现或分批止盈' : '持仓向止盈目标迈进中' }}</span>
      </div>

      <div class="summary-card">
        <span class="sc-label">指令单到位建仓</span>
        <span class="sc-val in-range">{{ decisions.summary?.in_range_count || 0 }} 只</span>
        <span class="sc-desc">现价进入计划建仓区间</span>
      </div>

      <div class="summary-card">
        <span class="sc-label">监控总持仓标的</span>
        <span class="sc-val">{{ decisions.summary?.total_holdings || 0 }} 只</span>
        <span class="sc-desc">盘中分钟级连续盯盘</span>
      </div>
    </div>

    <!-- 模块 1：持仓实时决策矩阵 -->
    <div class="section-box">
      <div class="sec-header">
        <h3 class="sec-title">💼 实盘持仓实时决策状态</h3>
        <span class="sec-tip">硬止损底线 -5.0%，目标止盈位 +15.0%</span>
      </div>

      <div class="table-wrapper">
        <table class="terminal-table">
          <thead>
            <tr>
              <th width="100">代码</th>
              <th width="110">名称</th>
              <th width="90">持仓成本</th>
              <th width="90">最新现价</th>
              <th width="90">今日涨跌</th>
              <th width="110">累计浮盈亏</th>
              <th width="130">即时操作指令</th>
              <th>盘中建议与决策逻辑</th>
              <th width="80">分时</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!decisions.holdings_decisions || decisions.holdings_decisions.length === 0">
              <td colspan="9" class="empty-cell">
                当前暂无持仓。请在「🛡️ 宏观持仓」中录入您的持仓标的，系统将自动进行实时盘中决策计算。
              </td>
            </tr>
            <tr
              v-for="item in decisions.holdings_decisions"
              :key="item.code"
              :class="{ 'row-urgent': item.status === 'STOP_LOSS', 'row-profit': item.status === 'TAKE_PROFIT' }"
            >
              <td class="code-col"><b>{{ item.code }}</b></td>
              <td><b>{{ item.name }}</b></td>
              <td class="num-col">¥{{ item.cost?.toFixed(2) }}</td>
              <td class="num-col"><b>¥{{ item.price?.toFixed(2) }}</b></td>
              <td :class="item.today_pct >= 0 ? 'text-up' : 'text-down'">
                {{ item.today_pct >= 0 ? '+' : '' }}{{ item.today_pct?.toFixed(2) }}%
              </td>
              <td class="pnl-col">
                <span class="pnl-val" :class="item.pnl_pct >= 0 ? 'text-up' : 'text-down'">
                  {{ item.pnl_pct >= 0 ? '+' : '' }}{{ (item.pnl_pct * 100)?.toFixed(2) }}%
                </span>
              </td>
              <td>
                <span class="action-tag" :class="item.status.toLowerCase()">
                  {{ item.action_badge }}
                </span>
              </td>
              <td class="advice-col">
                <span class="advice-text">{{ item.advice }}</span>
              </td>
              <td>
                <button class="kline-btn" @click="openKline(item)">📈 K线</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 模块 2：昨日指令单入场触发追踪 -->
    <div v-if="decisions.orders_triggers && decisions.orders_triggers.length > 0" class="section-box">
      <div class="sec-header">
        <h3 class="sec-title">📋 昨日指令单 · 盘中价格到位状态</h3>
      </div>
      <div class="triggers-grid">
        <div
          v-for="trig in decisions.orders_triggers"
          :key="trig.code"
          class="trigger-item"
          :class="trig.status.toLowerCase()"
        >
          <div class="t-top">
            <span class="t-name">{{ trig.name }} ({{ trig.code }})</span>
            <span class="t-badge">{{ trig.badge }}</span>
          </div>
          <span class="t-msg">{{ trig.message }}</span>
        </div>
      </div>
    </div>

    <!-- 模块 3：盘中即时异动与放量机会雷达 -->
    <div class="section-box">
      <div class="sec-header">
        <div class="sh-left">
          <h3 class="sec-title">📡 盘中即时异动雷达</h3>
          <span class="sec-tip">盘中高频实时扫描：早盘放量抢筹、均线上方多头动量异动标的</span>
        </div>
        <button class="small-btn" @click="loadRadar">🔄 刷新雷达</button>
      </div>

      <div class="table-wrapper">
        <table class="terminal-table">
          <thead>
            <tr>
              <th width="100">代码</th>
              <th width="120">名称</th>
              <th width="110">最新价格</th>
              <th width="110">日内涨跌幅</th>
              <th width="120">成交量</th>
              <th>异动标签与特征</th>
              <th width="90">动量得分</th>
              <th width="80">1分K</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="radarList.length === 0">
              <td colspan="8" class="empty-cell">
                {{ loading ? '正在扫描盘中多头异动与放量标的...' : '当前暂未出现符合高强度放量异动特征的标的。' }}
              </td>
            </tr>
            <tr
              v-for="r in radarList"
              :key="r.code"
              class="clickable-row"
              @click="openKline(r)"
            >
              <td class="code-col"><b>{{ r.code }}</b></td>
              <td>{{ r.name }}</td>
              <td class="num-col">¥{{ r.price?.toFixed(2) }}</td>
              <td :class="r.pct_change >= 0 ? 'text-up' : 'text-down'">
                <b>{{ r.pct_change >= 0 ? '+' : '' }}{{ r.pct_change?.toFixed(2) }}%</b>
              </td>
              <td class="num-col">{{ (r.volume / 100).toLocaleString() }}手</td>
              <td>
                <div class="tags-row">
                  <span v-for="tag in r.tags" :key="tag" class="radar-tag">{{ tag }}</span>
                </div>
              </td>
              <td>
                <span class="score-highlight">{{ r.score }}</span>
              </td>
              <td>
                <button class="kline-btn" @click.stop="openKline(r)">📈 看图</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- K线弹窗 -->
    <KLineModal
      v-model:visible="klineVisible"
      :stock="selectedStock"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import KLineModal from '../components/KLineModal.vue'

const loading = ref(false)
const decisions = ref({ holdings_decisions: [], orders_triggers: [], summary: {} })
const radarList = ref([])

const klineVisible = ref(false)
const selectedStock = ref(null)

async function loadData() {
  loading.value = true
  try {
    const [dRes, rRes] = await Promise.all([
      api.getIntradayDecisions(),
      api.getIntradayRadar()
    ])
    decisions.value = dRes || { holdings_decisions: [], orders_triggers: [], summary: {} }
    radarList.value = rRes || []
  } catch (err) {
    console.error('Failed to load intraday decisions:', err)
  } finally {
    loading.value = false
  }
}

async function loadRadar() {
  try {
    const res = await api.getIntradayRadar()
    radarList.value = res || []
  } catch (err) {
    console.error('Failed to reload radar:', err)
  }
}

function openKline(stock) {
  selectedStock.value = stock
  klineVisible.value = true
}

onMounted(() => {
  loadData()
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

.action-btn {
  background: #21262d;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.action-btn:hover:not(:disabled) {
  background: #30363d;
  color: #f0f6fc;
}

.summary-cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.summary-card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.summary-card.alert {
  border-color: rgba(248, 81, 73, 0.5);
  background: rgba(248, 81, 73, 0.1);
}

.summary-card.profit {
  border-color: rgba(63, 185, 80, 0.5);
  background: rgba(46, 160, 67, 0.1);
}

.sc-label {
  font-size: 11px;
  color: #8b949e;
}

.sc-val {
  font-size: 20px;
  font-weight: 700;
  color: #f0f6fc;
}

.sc-val.stop { color: #f85149; }
.sc-val.take { color: #3fb950; }
.sc-val.in-range { color: #58a6ff; }

.sc-desc {
  font-size: 11px;
  color: #8b949e;
}

.section-box {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sec-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.sec-title {
  font-size: 15px;
  font-weight: 600;
  color: #f0f6fc;
}

.sec-tip {
  font-size: 12px;
  color: #8b949e;
}

.sh-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.small-btn {
  background: #21262d;
  border: 1px solid #30363d;
  color: #58a6ff;
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
}

.table-wrapper {
  border: 1px solid #30363d;
  border-radius: 6px;
  overflow: hidden;
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
  padding: 8px 12px;
  border-bottom: 1px solid #30363d;
  font-size: 12px;
}

.terminal-table td {
  padding: 8px 12px;
  border-bottom: 1px solid #21262d;
  color: #c9d1d9;
}

.row-urgent {
  background: rgba(248, 81, 73, 0.08);
}

.row-profit {
  background: rgba(46, 160, 67, 0.08);
}

.code-col {
  color: #58a6ff;
  font-family: monospace;
}

.num-col {
  font-family: monospace;
}

.text-up {
  color: #ef4444;
}

.text-down {
  color: #10b981;
}

.pnl-val {
  font-weight: 700;
  font-family: monospace;
}

.action-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
}

.action-tag.stop_loss {
  background: rgba(248, 81, 73, 0.2);
  color: #f85149;
  border: 1px solid rgba(248, 81, 73, 0.4);
}

.action-tag.take_profit {
  background: rgba(46, 160, 67, 0.2);
  color: #3fb950;
  border: 1px solid rgba(63, 185, 80, 0.4);
}

.action-tag.warning_loss {
  background: rgba(210, 153, 34, 0.2);
  color: #d29922;
  border: 1px solid rgba(210, 153, 34, 0.4);
}

.action-tag.approach_profit {
  background: rgba(234, 179, 8, 0.2);
  color: #eab308;
  border: 1px solid rgba(234, 179, 8, 0.4);
}

.action-tag.safe_hold {
  background: #21262d;
  color: #8b949e;
  border: 1px solid #30363d;
}

.advice-col {
  font-size: 12px;
  color: #e2e8f0;
}

.kline-btn {
  background: #21262d;
  border: 1px solid #30363d;
  color: #58a6ff;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
}

.triggers-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 10px;
}

.trigger-item {
  padding: 10px 12px;
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.trigger-item.in_range {
  background: rgba(56, 139, 253, 0.1);
  border: 1px solid rgba(56, 139, 253, 0.3);
}

.trigger-item.limit_up_abort {
  background: rgba(210, 153, 34, 0.1);
  border: 1px solid rgba(210, 153, 34, 0.3);
}

.t-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.t-name {
  font-size: 13px;
  font-weight: 700;
  color: #f0f6fc;
}

.t-badge {
  font-size: 11px;
  font-weight: 600;
}

.t-msg {
  font-size: 12px;
  color: #c9d1d9;
}

.tags-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.radar-tag {
  background: #21262d;
  border: 1px solid #30363d;
  color: #58a6ff;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
}

.score-highlight {
  font-family: monospace;
  font-weight: 700;
  color: #eab308;
}

.empty-cell {
  text-align: center;
  padding: 30px;
  color: #8b949e;
}
</style>
