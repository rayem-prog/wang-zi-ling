<template>
  <div class="view-container">
    <div class="view-toolbar">
      <div class="toolbar-left">
        <h2 class="view-title">🛡️ 宏观温度计与持仓风控</h2>
        <span class="view-subtitle">M1+M4 宏观档位闸门、持仓健康度诊断及模拟盘账本</span>
      </div>
      <div class="toolbar-right">
        <button class="action-btn" @click="loadAll" :disabled="loading">
          {{ loading ? '加载中...' : '🔄 刷新全部' }}
        </button>
      </div>
    </div>

    <!-- 宏观温度计总览 -->
    <div class="macro-cards-grid">
      <div class="macro-card main-stance" :class="stanceClass">
        <div class="card-icon">{{ stanceIcon }}</div>
        <div class="card-content">
          <span class="card-sub">宏观总控档位</span>
          <span class="card-main">{{ stanceText }}</span>
          <span class="card-desc">总仓上限 {{ Math.round((macroData.current?.cap || 0.6) * 100) }}% · {{ macroData.current?.allow_new_buy ? '允许正常开仓' : '严禁新开仓' }}</span>
        </div>
      </div>

      <div class="macro-card">
        <span class="card-sub">综合评分</span>
        <span class="card-main score">{{ (macroData.current?.score || 0).toFixed(2) }}</span>
        <span class="card-desc">区间 [-1.0, +1.0]</span>
      </div>

      <div class="macro-card">
        <span class="card-sub">指数趋势分</span>
        <span class="card-main">{{ (macroData.current?.trend || 0).toFixed(2) }}</span>
        <span class="card-desc">权重 50%</span>
      </div>

      <div class="macro-card">
        <span class="card-sub">市场宽度分</span>
        <span class="card-main">{{ (macroData.current?.breadth || 0).toFixed(2) }}</span>
        <span class="card-desc">权重 30%</span>
      </div>

      <div class="macro-card">
        <span class="card-sub">波动惩罚分</span>
        <span class="card-main">{{ (macroData.current?.vol || 0).toFixed(2) }}</span>
        <span class="card-desc">权重 20%</span>
      </div>
    </div>

    <!-- 事件提醒横幅 -->
    <div v-if="macroData.event_status?.is_near_event" class="event-banner">
      <span>🔔 <b>重大事件防御期：</b> 检测到临近 [{{ macroData.event_status.event_names.join(', ') }}]，宏观档位已自动动态下调一档！</span>
    </div>

    <!-- 组合持仓与诊断 -->
    <div class="section-card">
      <div class="section-header">
        <h3 class="section-title">💼 实盘持仓健康诊断</h3>
        <button class="small-btn" @click="showAddPos = !showAddPos">
          {{ showAddPos ? '取消录入' : '➕ 录入新持仓' }}
        </button>
      </div>

      <!-- 快速录入表单 -->
      <div v-if="showAddPos" class="add-pos-form">
        <div style="flex: 2;">
          <StockSearchInput
            v-model="searchKeyword"
            @select="onStockSelect"
            placeholder="搜索输入拼音首字母(如PAYH)、代码或名称..."
          />
        </div>
        <div style="flex: 1.2; display: flex; gap: 8px;">
          <input v-model.number="newPos.shares" type="number" placeholder="股数(如1000)" class="terminal-input" />
          <input v-model.number="newPos.cost" type="number" step="0.01" placeholder="成本价(元)" class="terminal-input" />
        </div>
        <button class="action-btn primary" :disabled="!newPos.code" @click="savePosition">
          确认录入 {{ newPos.code ? `(${newPos.code})` : '' }}
        </button>
      </div>

      <!-- 持仓诊断表格 -->
      <div class="table-wrapper">
        <table class="terminal-table">
          <thead>
            <tr>
              <th width="100">代码</th>
              <th width="120">名称</th>
              <th width="100">持仓股数</th>
              <th width="100">成本价</th>
              <th width="100">最新价</th>
              <th width="110">浮动盈亏</th>
              <th width="120">风格分类</th>
              <th>系统诊断建议</th>
              <th width="80">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="holdingsData.positions.length === 0">
              <td colspan="9" class="empty-cell">暂无持仓记录。可通过上方按钮添加您的持仓以获取实时诊断。</td>
            </tr>
            <tr v-for="pos in holdingsData.diagnostics" :key="pos.code">
              <td class="code-col"><b>{{ pos.code }}</b></td>
              <td>{{ pos.name }}</td>
              <td class="num-col">{{ Number(pos.shares).toLocaleString() }}</td>
              <td class="num-col">¥{{ Number(pos.cost).toFixed(2) }}</td>
              <td class="num-col">¥{{ Number(pos.price || pos.cost).toFixed(2) }}</td>
              <td :class="(pos.pnl_pct || 0) >= 0 ? 'text-up' : 'text-down'">
                {{ ((pos.pnl_pct || 0) * 100).toFixed(2) }}%
              </td>
              <td>
                <span class="style-tag" :class="pos.style === '题材' ? 'theme' : 'value'">
                  {{ pos.style || '价值' }}
                </span>
              </td>
              <td>
                <span class="diag-tag">{{ pos.recommendation || pos.action || '持股观望' }}</span>
              </td>
              <td>
                <button class="del-btn" @click="deletePosition(pos.code)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 风险预警条目 -->
      <div v-if="holdingsData.warnings && holdingsData.warnings.length > 0" class="warnings-box">
        <div v-for="(w, i) in holdingsData.warnings" :key="i" class="warning-item">
          ⚠️ {{ w }}
        </div>
      </div>
    </div>

    <!-- 模拟盘账本概览 -->
    <div class="section-card">
      <div class="section-header">
        <h3 class="section-title">📝 模拟盘总览 (Paper Ledger)</h3>
      </div>
      <div class="paper-stats">
        <div class="p-stat">
          <span class="p-lbl">总资产估值</span>
          <span class="p-val">¥{{ (paperData.total_equity || 0).toLocaleString() }}</span>
        </div>
        <div class="p-stat">
          <span class="p-lbl">可用现金</span>
          <span class="p-val">¥{{ (paperData.cash || 0).toLocaleString() }}</span>
        </div>
        <div class="p-stat">
          <span class="p-lbl">持仓市值</span>
          <span class="p-val">¥{{ (paperData.market_value || 0).toLocaleString() }}</span>
        </div>
        <div class="p-stat">
          <span class="p-lbl">累计盈亏率</span>
          <span class="p-val" :class="(paperData.total_return || 0) >= 0 ? 'text-up' : 'text-down'">
            {{ ((paperData.total_return || 0) * 100).toFixed(2) }}%
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import StockSearchInput from '../components/StockSearchInput.vue'

const loading = ref(false)
const macroData = ref({})
const holdingsData = ref({ positions: [], diagnostics: [], warnings: [] })
const paperData = ref({})
const showAddPos = ref(false)
const searchKeyword = ref('')

const newPos = ref({
  code: '',
  name: '',
  shares: 1000,
  cost: 10.0
})

function onStockSelect(stock) {
  newPos.value.code = stock.code
  newPos.value.name = stock.name
  if (stock.price > 0) {
    newPos.value.cost = stock.price
  }
}

async function loadAll() {
  loading.value = true
  try {
    const [mRes, hRes, pRes] = await Promise.all([
      api.getMacro(),
      api.getHoldings(),
      api.getPaper()
    ])
    macroData.value = mRes || {}
    holdingsData.value = hRes || { positions: [], diagnostics: [], warnings: [] }
    paperData.value = pRes || {}
  } catch (err) {
    console.error('Failed to load macro & holdings:', err)
  } finally {
    loading.value = false
  }
}

const stanceClass = computed(() => {
  const s = macroData.value.current?.stance || 'NEUTRAL'
  if (s === 'ATTACK') return 'attack'
  if (s === 'DEFENSE') return 'defense'
  return 'neutral'
})

const stanceIcon = computed(() => {
  const s = macroData.value.current?.stance || 'NEUTRAL'
  if (s === 'ATTACK') return '🚀'
  if (s === 'DEFENSE') return '🛡️'
  return '⚖️'
})

const stanceText = computed(() => {
  const s = macroData.value.current?.stance || 'NEUTRAL'
  if (s === 'ATTACK') return '进攻档 (80%仓)'
  if (s === 'DEFENSE') return '防守档 (30%仓)'
  return '中性档 (60%仓)'
})

async function savePosition() {
  if (!newPos.value.code || !newPos.value.name) return
  try {
    await api.savePosition(newPos.value)
    showAddPos.value = false
    newPos.value = { code: '', name: '', shares: 1000, cost: 10.0 }
    searchKeyword.value = ''
    loadAll()
  } catch (err) {
    alert('保存持仓失败: ' + err)
  }
}

async function deletePosition(code) {
  if (!confirm(`确认删除股票 ${code} 的持仓记录？`)) return
  try {
    await api.deletePosition(code)
    loadAll()
  } catch (err) {
    alert('删除失败: ' + err)
  }
}

onMounted(() => {
  loadAll()
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
}

.action-btn.primary {
  background: #238636;
  border-color: #2ea043;
  color: #ffffff;
}

.action-btn.primary:hover {
  background: #2ea043;
}

.macro-cards-grid {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr 1fr 1fr;
  gap: 12px;
}

.macro-card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.main-stance {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 14px;
}

.main-stance.attack {
  border-color: rgba(63, 185, 80, 0.4);
  background: rgba(46, 160, 67, 0.1);
}

.main-stance.neutral {
  border-color: rgba(88, 166, 255, 0.4);
  background: rgba(56, 139, 253, 0.1);
}

.main-stance.defense {
  border-color: rgba(248, 81, 73, 0.4);
  background: rgba(248, 81, 73, 0.1);
}

.card-icon {
  font-size: 32px;
}

.card-sub {
  font-size: 11px;
  color: #8b949e;
}

.card-main {
  font-size: 18px;
  font-weight: 700;
  color: #f0f6fc;
}

.card-main.score {
  color: #58a6ff;
}

.card-desc {
  font-size: 11px;
  color: #8b949e;
}

.event-banner {
  background: rgba(210, 153, 34, 0.15);
  border: 1px solid rgba(210, 153, 34, 0.4);
  color: #d29922;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
}

.section-card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: #f0f6fc;
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

.add-pos-form {
  display: flex;
  gap: 10px;
  padding: 10px;
  background: #0d1117;
  border-radius: 6px;
}

.terminal-input {
  background: #161b22;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 6px 10px;
  border-radius: 4px;
  font-size: 12px;
  flex: 1;
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

.code-col {
  color: #58a6ff;
  font-family: monospace;
}

.num-col {
  font-family: monospace;
}

.text-up {
  color: #ef4444;
  font-weight: 600;
  font-family: monospace;
}

.text-down {
  color: #10b981;
  font-weight: 600;
  font-family: monospace;
}

.style-tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
}

.style-tag.theme {
  background: rgba(168, 85, 247, 0.2);
  color: #c084fc;
}

.style-tag.value {
  background: rgba(56, 139, 253, 0.2);
  color: #79c0ff;
}

.diag-tag {
  font-size: 11px;
  background: #21262d;
  padding: 2px 6px;
  border-radius: 4px;
  color: #c9d1d9;
}

.del-btn {
  background: transparent;
  border: none;
  color: #f85149;
  font-size: 12px;
  cursor: pointer;
}

.warnings-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.warning-item {
  background: rgba(210, 153, 34, 0.1);
  border-left: 3px solid #d29922;
  padding: 6px 12px;
  font-size: 12px;
  color: #d29922;
}

.paper-stats {
  display: flex;
  gap: 16px;
}

.p-stat {
  flex: 1;
  background: #0d1117;
  border-radius: 6px;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.p-lbl {
  font-size: 11px;
  color: #8b949e;
}

.p-val {
  font-size: 16px;
  font-weight: 700;
  color: #f0f6fc;
}

.empty-cell {
  text-align: center;
  padding: 24px;
  color: #8b949e;
}
</style>
