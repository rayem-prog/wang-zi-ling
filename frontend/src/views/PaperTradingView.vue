<template>
  <div class="view-container">
    <!-- 顶部工具栏与场景随机发生器 -->
    <div class="view-toolbar">
      <div class="toolbar-left">
        <h2 class="view-title">🎮 模拟交易与模拟账户中心</h2>
        <span class="view-subtitle">真实资金记账、价格档位撮合下单、AI 自主推演模拟建仓与行情压力测试</span>
      </div>
      <div class="toolbar-right">
        <button class="action-btn random-btn" @click="handleRandomPortfolio" :disabled="loading" title="随机生成 3-5 只代表性真实股票持仓组合">
          🎲 随机生成持仓
        </button>
        <button class="action-btn shock-btn" @click="handleShockTest" :disabled="loading" title="随机扰动当前持仓价格(-7%至+17%)，测试止损止盈预警">
          ⚡ 随机行情震荡 (压力测试)
        </button>
        <button class="action-btn reset-btn" @click="handleResetAccount" :disabled="loading" title="清空持仓，重置为初始 100 万现金">
          🔄 重置账户
        </button>
      </div>
    </div>

    <!-- 模拟账户资产概览 -->
    <div class="account-summary-grid">
      <div class="acc-card main">
        <span class="acc-sub">模拟总资产估值</span>
        <span class="acc-val total">¥{{ account.total_equity?.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
        <span class="acc-desc">初始资金: ¥{{ account.initial_cash?.toLocaleString() }}</span>
      </div>

      <div class="acc-card">
        <span class="acc-sub">可用现金</span>
        <span class="acc-val cash">¥{{ account.cash?.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
        <span class="acc-desc">随时可支配买入资金</span>
      </div>

      <div class="acc-card">
        <span class="acc-sub">持仓总市值</span>
        <span class="acc-val">¥{{ account.market_value?.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
        <span class="acc-desc">当前仓位占比 {{ positionRatio }}%</span>
      </div>

      <div class="acc-card">
        <span class="acc-sub">累计实现/浮动盈亏</span>
        <span class="acc-val" :class="account.total_pnl >= 0 ? 'text-up' : 'text-down'">
          {{ account.total_pnl >= 0 ? '+' : '' }}¥{{ account.total_pnl?.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}
        </span>
        <span class="acc-desc" :class="account.return_pct >= 0 ? 'text-up' : 'text-down'">
          总收益率: {{ account.return_pct >= 0 ? '+' : '' }}{{ (account.return_pct * 100)?.toFixed(2) }}%
        </span>
      </div>
    </div>

    <!-- 🤖 AI 自主模拟与智能仓位推演中心 -->
    <div class="ai-pilot-section">
      <div class="ai-section-header">
        <div class="header-left">
          <div class="ai-title-row">
            <span class="ai-badge">AI AUTO-PILOT</span>
            <h3 class="ai-title">🤖 AI 自主模拟推演 · 价格区间与推荐股数</h3>
          </div>
          <span class="ai-subtitle">
            AI 依据账户当前可用现金 <b>¥{{ account.cash?.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</b>、多因子胜率评分与风控模型，动态测算推荐标的、挂单价格区间与最优推荐股数
          </span>
        </div>

        <div class="header-right">
          <!-- 风险偏好切换器 -->
          <div class="risk-pref-selector">
            <button
              :class="{ active: currentRiskPref === 'conservative' }"
              @click="switchRiskPref('conservative')"
              title="单票上限 15%，防守控制回撤"
            >
              🛡️ 稳健型 (15%仓)
            </button>
            <button
              :class="{ active: currentRiskPref === 'balanced' }"
              @click="switchRiskPref('balanced')"
              title="单票上限 25%，风险收益平衡"
            >
              ⚖️ 平衡型 (25%仓)
            </button>
            <button
              :class="{ active: currentRiskPref === 'aggressive' }"
              @click="switchRiskPref('aggressive')"
              title="单票上限 35%，进取弹性博弈"
            >
              🚀 进取型 (35%仓)
            </button>
          </div>

          <button class="ai-action-btn refresh" @click="fetchAiSimulation" :disabled="aiLoading">
            {{ aiLoading ? '推演中...' : '🔄 重新生成 AI 推演' }}
          </button>
          <button
            class="ai-action-btn execute"
            @click="handleExecuteAiPlan"
            :disabled="aiExecuting || !aiPlan.recommendations?.length"
          >
            {{ aiExecuting ? '执行中...' : '⚡ AI 一键全自动建仓' }}
          </button>
        </div>
      </div>

      <!-- AI 推荐标的网格列表 -->
      <div class="ai-cards-grid" v-if="aiPlan.recommendations && aiPlan.recommendations.length > 0">
        <div
          v-for="rec in aiPlan.recommendations"
          :key="rec.code"
          class="ai-rec-card"
          :class="{ 'is-held': rec.is_held }"
        >
          <div class="card-top">
            <div class="stock-meta">
              <span class="stock-name">{{ rec.name }}</span>
              <span class="stock-code">{{ rec.code }}</span>
              <span class="held-tag" v-if="rec.is_held">已持仓</span>
            </div>
            <div class="stock-price-block">
              <span class="price-val">现价 ¥{{ rec.current_price?.toFixed(2) }}</span>
              <span class="score-badge">胜率分 {{ rec.score }}</span>
            </div>
          </div>

          <div class="card-metrics">
            <!-- 核心 1: AI 建议价格区间 -->
            <div class="metric-box price-range">
              <span class="m-label">🎯 AI 建议建仓区间</span>
              <span class="m-val highlight-range">
                ¥{{ rec.price_range_low?.toFixed(2) }} ~ ¥{{ rec.price_range_high?.toFixed(2) }}
              </span>
              <span class="m-sub">建议挂单中枢: ¥{{ rec.optimal_entry?.toFixed(2) }}</span>
            </div>

            <!-- 核心 2: AI 推荐股数与资金占比 -->
            <div class="metric-box shares">
              <span class="m-label">📦 AI 推荐买入股数</span>
              <span class="m-val highlight-shares">
                {{ rec.recommended_shares?.toLocaleString() }} 股
              </span>
              <span class="m-sub">预估 ¥{{ rec.estimated_amount?.toLocaleString() }} (占总资产 {{ rec.position_pct }}%)</span>
            </div>
          </div>

          <!-- 阶梯挂单拆分建议 -->
          <div class="ladder-box" v-if="rec.ladder && rec.ladder.length">
            <div class="ladder-title">📊 阶梯分批挂单建议：</div>
            <div class="ladder-items">
              <div v-for="(l, i) in rec.ladder" :key="i" class="ladder-item">
                <span class="l-tier">{{ l.tier }}</span>
                <span class="l-val">挂单 ¥{{ l.price }} × <b>{{ l.shares }}股</b> (¥{{ l.amount?.toLocaleString() }})</span>
              </div>
            </div>
          </div>

          <!-- 止损与目标价 -->
          <div class="target-stop-row">
            <span class="target-tag">🟢 目标止盈: ¥{{ rec.target_price }} (+15%)</span>
            <span class="stop-tag">🔴 硬止损线: ¥{{ rec.stop_loss_price }} (-5%)</span>
          </div>

          <!-- AI 决策理由简析 -->
          <div class="ai-rationale-text">
            {{ rec.ai_rationale }}
          </div>

          <!-- 卡片底部快捷操作 -->
          <div class="card-actions">
            <button class="adopt-btn" @click="adoptAiRecommendation(rec)" title="将此标的、最优价格档位与推荐股数一键填入左侧下单表">
              📥 采纳并填入下单表
            </button>
            <button class="kline-card-btn" @click="openKline(rec)" title="查看高清专业 K 线图与区间对照">
              📊 查看 K 线
            </button>
          </div>
        </div>
      </div>

      <div v-else-if="aiLoading" class="ai-empty-box">
        <span class="spinner"></span>
        <span>AI 正在结合当前可用资金与多因子模型进行深度推演计算...</span>
      </div>

      <div v-else class="ai-empty-box">
        <span>当前无推荐标的或可用资金过低。可点击右上角「🔄 重置账户」重置 100 万现金后重新推演。</span>
      </div>
    </div>

    <!-- 交易主操作区：左侧下单与价格档位，右侧当前持仓明细 -->
    <div class="trade-workspace">
      <!-- 左侧：价格档位选择与模拟下单 -->
      <div class="order-box">
        <div class="box-header">
          <h3 class="box-title">📝 模拟交易委托下单</h3>
          <span class="box-tip">支持五种价格档位快速委托</span>
        </div>

        <div class="order-form">
          <!-- 1. 标的选择 -->
          <div class="form-item">
            <label class="form-label">交易标的：</label>
            <StockSearchInput
              v-model="orderSearchKey"
              @select="onStockSelected"
              placeholder="输入拼音(如PAYH)、代码或名称..."
            />
            <div v-if="selectedStock" class="selected-stock-info">
              <span>已选: <b>{{ selectedStock.name }} ({{ selectedStock.code }})</b></span>
              <span class="cur-price">现价: ¥{{ selectedStock.price?.toFixed(2) || '--' }}</span>
              <button class="mini-kline-btn" @click="openKline(selectedStock)">📊 查看K线</button>
            </div>
          </div>

          <!-- 2. 价格档位选择 -->
          <div class="form-item">
            <label class="form-label">委托价格档位选择：</label>
            <div class="tiers-buttons">
              <button
                v-for="t in priceTiers"
                :key="t.key"
                class="tier-btn"
                :class="{ active: currentTier === t.key }"
                @click="selectPriceTier(t)"
              >
                {{ t.label }}
              </button>
            </div>
            <div class="custom-price-input" v-if="currentTier === 'custom'">
              <input
                v-model.number="orderForm.price"
                type="number"
                step="0.01"
                placeholder="输入自定义委托价格"
                class="terminal-input"
              />
            </div>
            <div class="tier-hint" v-else>
              <span>当前选定委托价: <b class="highlight-price">¥{{ orderForm.price?.toFixed(2) || '--' }}</b></span>
            </div>
          </div>

          <!-- 3. 股数与快捷仓位百分比 -->
          <div class="form-item">
            <div class="shares-header">
              <label class="form-label">委托股数：</label>
              <div class="quick-ratios">
                <button class="ratio-btn" @click="applyRatio(0.25)">25%仓</button>
                <button class="ratio-btn" @click="applyRatio(0.50)">半仓</button>
                <button class="ratio-btn" @click="applyRatio(0.75)">75%仓</button>
                <button class="ratio-btn" @click="applyRatio(1.00)">全仓</button>
              </div>
            </div>
            <input
              v-model.number="orderForm.shares"
              type="number"
              step="100"
              min="100"
              placeholder="股数(100整数倍)"
              class="terminal-input"
            />
            <div class="order-calc">
              <span>预估交易金额: <b>¥{{ (orderForm.shares * orderForm.price || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</b></span>
            </div>
          </div>

          <!-- 4. 买卖操作按钮 -->
          <div class="action-buttons-row">
            <button
              class="trade-btn buy"
              :disabled="!canBuy || ordering"
              @click="submitOrder('买入')"
            >
              🔴 模拟买入建仓
            </button>
            <button
              class="trade-btn sell"
              :disabled="!canSell || ordering"
              @click="submitOrder('卖出')"
            >
              🟢 模拟卖出平仓
            </button>
          </div>
        </div>
      </div>

      <!-- 右侧：当前持仓列表 -->
      <div class="positions-box">
        <div class="box-header">
          <h3 class="box-title">💼 当前模拟持仓明细 ({{ account.positions?.length || 0 }} 只)</h3>
          <span class="box-tip">支持查看高清K线、一键平仓与半仓调仓</span>
        </div>

        <div class="table-wrapper">
          <table class="terminal-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>名称</th>
                <th>持仓股数</th>
                <th>持仓成本</th>
                <th>最新价</th>
                <th>持仓市值</th>
                <th>浮动盈亏</th>
                <th>快捷操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!account.positions || account.positions.length === 0">
                <td colspan="8" class="empty-cell">
                  当前暂无持仓。可通过上方 AI 自主推演「📥 采纳下单」或点击顶部「🎲 随机生成持仓」一键初始化体验！
                </td>
              </tr>
              <tr v-for="p in account.positions" :key="p.code">
                <td class="code-col"><b>{{ p.code }}</b></td>
                <td><b>{{ p.name }}</b></td>
                <td class="num-col">{{ Number(p.shares).toLocaleString() }}</td>
                <td class="num-col">¥{{ p.avg_cost?.toFixed(2) }}</td>
                <td class="num-col">¥{{ p.price?.toFixed(2) }}</td>
                <td class="num-col">¥{{ p.market_value?.toLocaleString() }}</td>
                <td :class="p.pnl >= 0 ? 'text-up' : 'text-down'">
                  <b>{{ p.pnl >= 0 ? '+' : '' }}{{ (p.pnl_pct * 100)?.toFixed(2) }}%</b>
                  <br />
                  <span class="sub-pnl">({{ p.pnl >= 0 ? '+' : '' }}¥{{ p.pnl?.toFixed(0) }})</span>
                </td>
                <td>
                  <div class="quick-actions">
                    <button class="mini-btn kline" @click="openKline(p)" title="查看高清 K 线">📊 K线</button>
                    <button class="mini-btn sell" @click="quickSell(p, 1.0)">平仓</button>
                    <button class="mini-btn half" @click="quickSell(p, 0.5)">半仓</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 底部：历史交易成交流水 -->
    <div class="history-section">
      <div class="box-header">
        <h3 class="box-title">📜 模拟交易历史成交流水</h3>
      </div>
      <div class="table-wrapper">
        <table class="terminal-table">
          <thead>
            <tr>
              <th width="140">时间</th>
              <th width="80">方向</th>
              <th width="110">档位类型</th>
              <th width="100">代码</th>
              <th width="110">名称</th>
              <th width="100">成交股数</th>
              <th width="100">成交价格</th>
              <th width="120">成交金额</th>
              <th width="80">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!account.orders || account.orders.length === 0">
              <td colspan="9" class="empty-cell">暂无模拟交易成交流水。</td>
            </tr>
            <tr v-for="o in account.orders" :key="o.id">
              <td class="time-col">{{ o.timestamp }}</td>
              <td>
                <span class="side-badge" :class="o.side === '买入' ? 'buy' : 'sell'">{{ o.side }}</span>
              </td>
              <td><span class="type-tag">{{ o.order_type || '市价' }}</span></td>
              <td class="code-col">{{ o.code }}</td>
              <td>{{ o.name }}</td>
              <td class="num-col">{{ Number(o.shares).toLocaleString() }}</td>
              <td class="num-col">¥{{ Number(o.price).toFixed(2) }}</td>
              <td class="num-col">¥{{ Number(o.amount).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</td>
              <td><span class="status-done">已成交</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 高清 K 线弹窗 -->
    <KLineModal
      v-model:visible="klineVisible"
      :stock="klineStock"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import StockSearchInput from '../components/StockSearchInput.vue'
import KLineModal from '../components/KLineModal.vue'

const loading = ref(false)
const ordering = ref(false)
const account = ref({
  initial_cash: 1000000,
  cash: 1000000,
  market_value: 0,
  total_equity: 1000000,
  total_pnl: 0,
  return_pct: 0,
  positions: [],
  orders: []
})

// K 线弹窗状态
const klineVisible = ref(false)
const klineStock = ref(null)

function openKline(stock) {
  if (!stock?.code) return
  klineStock.value = {
    code: stock.code,
    name: stock.name,
    price: stock.price || stock.current_price || stock.avg_cost || 0
  }
  klineVisible.value = true
}

// AI 自主推演状态
const currentRiskPref = ref('balanced')
const aiLoading = ref(false)
const aiExecuting = ref(false)
const aiPlan = ref({
  status: '',
  risk_pref: 'balanced',
  risk_label: '平衡稳进型',
  available_cash: 0,
  total_equity: 0,
  recommendations: []
})

const orderSearchKey = ref('')
const selectedStock = ref(null)

const priceTiers = [
  { key: 'market', label: '市价(最新价)', delta: 0.0 },
  { key: 'bid1', label: '买一档(-0.2%)', delta: -0.002 },
  { key: 'bid2', label: '买二档(-0.5%)', delta: -0.005 },
  { key: 'ask1', label: '卖一档(+0.3%)', delta: 0.003 },
  { key: 'custom', label: '自定义限价', delta: 0.0 },
]
const currentTier = ref('market')

const orderForm = ref({
  code: '',
  name: '',
  price: 10.0,
  shares: 1000,
  order_type: '市价'
})

const positionRatio = computed(() => {
  if (!account.value.total_equity) return 0
  return Math.round((account.value.market_value / account.value.total_equity) * 100)
})

const canBuy = computed(() => {
  return orderForm.value.code && orderForm.value.shares > 0 && orderForm.value.price > 0
})

const canSell = computed(() => {
  if (!orderForm.value.code || orderForm.value.shares <= 0) return false
  const p = account.value.positions?.find(pos => pos.code === orderForm.value.code)
  return p && p.shares >= orderForm.value.shares
})

async function loadAccount() {
  loading.value = true
  try {
    const res = await api.getPaperAccount()
    if (res) account.value = res
  } catch (err) {
    console.error('Failed to load paper account:', err)
  } finally {
    loading.value = false
  }
}

async function fetchAiSimulation() {
  aiLoading.value = true
  try {
    const res = await api.getAiSimulation(currentRiskPref.value)
    if (res) {
      aiPlan.value = res
    }
  } catch (err) {
    console.error('Failed to fetch AI simulation:', err)
  } finally {
    aiLoading.value = false
  }
}

function switchRiskPref(pref) {
  if (currentRiskPref.value === pref) return
  currentRiskPref.value = pref
  fetchAiSimulation()
}

function adoptAiRecommendation(rec) {
  orderSearchKey.value = `${rec.name} ${rec.code}`
  selectedStock.value = {
    code: rec.code,
    name: rec.name,
    price: rec.optimal_entry
  }
  orderForm.value.code = rec.code
  orderForm.value.name = rec.name
  orderForm.value.price = rec.optimal_entry
  orderForm.value.shares = rec.recommended_shares
  orderForm.value.order_type = '买一档(-0.2%)'
  currentTier.value = 'bid1'

  // 平滑滚动至下单委托区域
  const el = document.querySelector('.order-box')
  if (el) el.scrollIntoView({ behavior: 'smooth' })
}

async function handleExecuteAiPlan() {
  const count = aiPlan.value.recommendations?.length || 0
  if (!count) return
  if (!confirm(`确认根据当前 AI 模拟推演结果，自动执行 ${count} 笔模拟建仓委托吗？`)) {
    return
  }
  aiExecuting.value = true
  try {
    const res = await api.executeAiSimulation({
      risk_pref: currentRiskPref.value,
      recommendations: aiPlan.value.recommendations
    })
    alert(`🎉 AI 模拟执行完成！成功撮合建仓 ${res.executed_count} 只标的，已全量入账。`)
    await loadAccount()
    await fetchAiSimulation()
  } catch (err) {
    alert(err.response?.data?.detail || 'AI 执行失败')
  } finally {
    aiExecuting.value = false
  }
}

function onStockSelected(stock) {
  selectedStock.value = stock
  orderForm.value.code = stock.code
  orderForm.value.name = stock.name
  if (stock.price > 0) {
    applyTierPrice(stock.price, currentTier.value)
  }
}

function selectPriceTier(tier) {
  currentTier.value = tier.key
  orderForm.value.order_type = tier.label
  if (selectedStock.value?.price && tier.key !== 'custom') {
    applyTierPrice(selectedStock.value.price, tier.key)
  }
}

function applyTierPrice(basePrice, tierKey) {
  const tier = priceTiers.find(t => t.key === tierKey)
  if (tier) {
    orderForm.value.price = roundToTick(basePrice * (1 + tier.delta))
  }
}

function roundToTick(val) {
  return Math.round(val * 100) / 100
}

function applyRatio(ratio) {
  if (!orderForm.value.price || orderForm.value.price <= 0) return
  const budget = account.value.cash * ratio
  const rawShares = budget / orderForm.value.price
  const shares = Math.max(100, Math.floor(rawShares / 100) * 100)
  orderForm.value.shares = shares
}

async function submitOrder(side) {
  if (!orderForm.value.code) {
    alert('请先选择交易标的！')
    return
  }
  ordering.value = true
  try {
    await api.placePaperOrder({
      code: orderForm.value.code,
      name: orderForm.value.name,
      side: side,
      shares: orderForm.value.shares,
      price: orderForm.value.price,
      order_type: orderForm.value.order_type
    })
    alert(`委托下单成功！已撮合成交 ${side} ${orderForm.value.name} ${orderForm.value.shares} 股`)
    await loadAccount()
    await fetchAiSimulation()
  } catch (err) {
    alert(err.response?.data?.detail || '下单失败')
  } finally {
    ordering.value = false
  }
}

async function quickSell(pos, ratio) {
  const sellShares = Math.max(100, Math.floor((pos.shares * ratio) / 100) * 100)
  const actionLabel = ratio === 1.0 ? '全额平仓' : '半仓减持'
  if (!confirm(`确定对 ${pos.name} (${pos.code}) 执行${actionLabel} (拟卖出 ${sellShares} 股) 吗？`)) {
    return
  }
  loading.value = true
  try {
    await api.placePaperOrder({
      code: pos.code,
      name: pos.name,
      side: '卖出',
      shares: sellShares,
      price: pos.price,
      order_type: '市价平仓'
    })
    alert(`平仓执行成功！`)
    await loadAccount()
    await fetchAiSimulation()
  } catch (err) {
    alert(err.response?.data?.detail || '平仓失败')
  } finally {
    loading.value = false
  }
}

async function handleRandomPortfolio() {
  loading.value = true
  try {
    const res = await api.makeRandomPortfolio()
    if (res) account.value = res
    alert('🎲 已成功随机生成并初始化持仓组合！')
    await fetchAiSimulation()
  } catch (err) {
    alert(err.response?.data?.detail || '随机生成持仓失败')
  } finally {
    loading.value = false
  }
}

async function handleShockTest() {
  loading.value = true
  try {
    const res = await api.shockPortfolio()
    if (res) account.value = res
    alert('⚡ 已施加极端行情震荡！请观察盈亏变化与止损止盈预警！')
    await fetchAiSimulation()
  } catch (err) {
    alert(err.response?.data?.detail || '压力测试失败')
  } finally {
    loading.value = false
  }
}

async function handleResetAccount() {
  if (!confirm('确定清空所有模拟持仓与委托历史，重置为 100 万元初始现金吗？')) return
  loading.value = true
  try {
    await api.resetPaperAccount()
    await loadAccount()
    await fetchAiSimulation()
    alert('🔄 模拟账户已成功重置为 100 万元初始现金！')
  } catch (err) {
    alert(err.response?.data?.detail || '重置失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadAccount()
  fetchAiSimulation()
})
</script>

<style scoped>
.view-container {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.view-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 14px 20px;
}

.view-title {
  font-size: 18px;
  font-weight: 700;
  color: #f0f6fc;
}

.view-subtitle {
  font-size: 12px;
  color: #8b949e;
  margin-top: 4px;
  display: block;
}

.toolbar-right {
  display: flex;
  gap: 12px;
}

.action-btn {
  background: #21262d;
  color: #c9d1d9;
  border: 1px solid #30363d;
  padding: 7px 14px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  font-weight: 600;
  transition: all 0.2s;
}

.action-btn:hover:not(:disabled) {
  border-color: #58a6ff;
  color: #58a6ff;
}

.action-btn.random-btn {
  background: rgba(168, 85, 247, 0.15);
  border-color: #a855f7;
  color: #d8b4fe;
}
.action-btn.random-btn:hover {
  background: rgba(168, 85, 247, 0.25);
}

.action-btn.shock-btn {
  background: rgba(234, 179, 8, 0.15);
  border-color: #eab308;
  color: #fde047;
}
.action-btn.shock-btn:hover {
  background: rgba(234, 179, 8, 0.25);
}

.action-btn.reset-btn {
  background: rgba(239, 68, 68, 0.15);
  border-color: #ef4444;
  color: #fca5a5;
}
.action-btn.reset-btn:hover {
  background: rgba(239, 68, 68, 0.25);
}

/* 账户资产网格 */
.account-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}

.acc-card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.acc-card.main {
  background: linear-gradient(145deg, #1c2331, #131822);
  border-color: #388bfd;
}

.acc-sub {
  font-size: 12px;
  color: #8b949e;
}

.acc-val {
  font-size: 22px;
  font-weight: 700;
  font-family: monospace;
  color: #f0f6fc;
}

.acc-val.total {
  color: #58a6ff;
}

.acc-val.cash {
  color: #3fb950;
}

.acc-desc {
  font-size: 11px;
  color: #8b949e;
}

/* 🤖 AI 自主模拟推演面板 */
.ai-pilot-section {
  background: #111722;
  border: 1px solid #1f6feb;
  border-radius: 10px;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  box-shadow: 0 4px 20px rgba(31, 111, 235, 0.1);
}

.ai-section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
  border-bottom: 1px solid #212d3d;
  padding-bottom: 14px;
}

.ai-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.ai-badge {
  background: linear-gradient(135deg, #1f6feb, #8a2be2);
  color: #ffffff;
  font-size: 10px;
  font-weight: 800;
  padding: 2px 7px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}

.ai-title {
  font-size: 17px;
  font-weight: 700;
  color: #f0f6fc;
}

.ai-subtitle {
  font-size: 12px;
  color: #8b949e;
  margin-top: 4px;
  display: block;
}

.ai-subtitle b {
  color: #58a6ff;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.risk-pref-selector {
  display: flex;
  background: #090d13;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 2px;
}

.risk-pref-selector button {
  background: transparent;
  border: none;
  color: #8b949e;
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.risk-pref-selector button.active {
  background: #1f6feb;
  color: #ffffff;
  font-weight: 600;
}

.ai-action-btn {
  border: none;
  padding: 7px 14px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s;
}

.ai-action-btn.refresh {
  background: #21262d;
  color: #58a6ff;
  border: 1px solid #388bfd;
}
.ai-action-btn.refresh:hover:not(:disabled) {
  background: rgba(56, 139, 253, 0.15);
}

.ai-action-btn.execute {
  background: linear-gradient(135deg, #238636, #2ea043);
  color: #ffffff;
  box-shadow: 0 2px 8px rgba(46, 160, 67, 0.4);
}
.ai-action-btn.execute:hover:not(:disabled) {
  filter: brightness(1.1);
}

/* AI 推荐卡片网格 */
.ai-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
}

.ai-rec-card {
  background: #161f2e;
  border: 1px solid #2a384c;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: all 0.2s;
}

.ai-rec-card:hover {
  border-color: #58a6ff;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}

.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.stock-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.stock-name {
  font-size: 16px;
  font-weight: 700;
  color: #f0f6fc;
}

.stock-code {
  font-size: 12px;
  font-family: monospace;
  color: #8b949e;
  background: #0d121a;
  padding: 1px 5px;
  border-radius: 4px;
}

.held-tag {
  font-size: 10px;
  background: rgba(56, 139, 253, 0.2);
  color: #58a6ff;
  padding: 1px 5px;
  border-radius: 4px;
}

.stock-price-block {
  text-align: right;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}

.price-val {
  font-size: 13px;
  color: #e2e8f0;
  font-family: monospace;
  font-weight: 600;
}

.score-badge {
  font-size: 10px;
  background: rgba(234, 179, 8, 0.2);
  color: #fde047;
  padding: 1px 5px;
  border-radius: 4px;
  font-weight: 600;
}

/* 核心两项度量 */
.card-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.metric-box {
  background: #0d131d;
  border: 1px solid #232f3e;
  border-radius: 6px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.metric-box .m-label {
  font-size: 11px;
  color: #8b949e;
}

.highlight-range {
  font-size: 13px;
  font-weight: 700;
  color: #58a6ff;
  font-family: monospace;
}

.highlight-shares {
  font-size: 15px;
  font-weight: 700;
  color: #f59e0b;
  font-family: monospace;
}

.metric-box .m-sub {
  font-size: 10px;
  color: #8b949e;
}

/* 阶梯挂单建议 */
.ladder-box {
  background: rgba(13, 19, 29, 0.6);
  border: 1px dashed #2a384c;
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 11px;
}

.ladder-title {
  color: #8b949e;
  margin-bottom: 4px;
  font-weight: 600;
}

.ladder-items {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ladder-item {
  display: flex;
  justify-content: space-between;
  color: #c9d1d9;
}

.ladder-item .l-tier {
  color: #8b949e;
}

.ladder-item .l-val b {
  color: #f59e0b;
}

.target-stop-row {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  background: #090e16;
  padding: 6px 10px;
  border-radius: 4px;
}

.target-tag {
  color: #00e676;
}

.stop-tag {
  color: #ff4d4f;
}

.ai-rationale-text {
  font-size: 12px;
  line-height: 1.5;
  color: #c9d1d9;
  background: rgba(22, 27, 34, 0.5);
  border-left: 3px solid #1f6feb;
  padding: 6px 10px;
  border-radius: 0 4px 4px 0;
}

.card-actions {
  display: flex;
  gap: 10px;
  margin-top: 4px;
}

.adopt-btn {
  flex: 1;
  background: #238636;
  color: #ffffff;
  border: none;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s;
}

.adopt-btn:hover {
  background: #2ea043;
}

.kline-card-btn {
  background: #21262d;
  color: #58a6ff;
  border: 1px solid #30363d;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.kline-card-btn:hover {
  border-color: #58a6ff;
  background: rgba(56, 139, 253, 0.1);
}

.ai-empty-box {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 30px;
  color: #8b949e;
  font-size: 13px;
}

/* 交易主工作区 */
.trade-workspace {
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: 16px;
}

.order-box, .positions-box, .history-section {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.box-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid #21262d;
  padding-bottom: 10px;
}

.box-title {
  font-size: 15px;
  font-weight: 700;
  color: #f0f6fc;
}

.box-tip {
  font-size: 11px;
  color: #8b949e;
}

.order-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-label {
  font-size: 13px;
  font-weight: 600;
  color: #c9d1d9;
}

.selected-stock-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #0d1117;
  border: 1px solid #21262d;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 12px;
}

.selected-stock-info .cur-price {
  color: #ef4444;
  font-weight: 600;
  font-family: monospace;
}

.mini-kline-btn {
  background: #21262d;
  color: #58a6ff;
  border: 1px solid #30363d;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  cursor: pointer;
}
.mini-kline-btn:hover {
  border-color: #58a6ff;
}

.tiers-buttons {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
}

.tier-btn {
  background: #0d1117;
  border: 1px solid #30363d;
  color: #8b949e;
  border-radius: 6px;
  padding: 7px 4px;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.2s;
  text-align: center;
}

.tier-btn.active {
  background: #1f6feb;
  border-color: #58a6ff;
  color: #ffffff;
  font-weight: 600;
}

.tier-hint {
  font-size: 12px;
  color: #8b949e;
  margin-top: 2px;
}

.highlight-price {
  color: #58a6ff;
  font-size: 14px;
  font-family: monospace;
}

.shares-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.quick-ratios {
  display: flex;
  gap: 4px;
}

.ratio-btn {
  background: #21262d;
  border: 1px solid #30363d;
  color: #8b949e;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  cursor: pointer;
}

.ratio-btn:hover {
  color: #58a6ff;
  border-color: #58a6ff;
}

.terminal-input {
  background: #0d1117;
  border: 1px solid #30363d;
  color: #f0f6fc;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 13px;
  font-family: monospace;
  outline: none;
}

.terminal-input:focus {
  border-color: #58a6ff;
}

.order-calc {
  font-size: 12px;
  color: #8b949e;
  margin-top: 2px;
}

.order-calc b {
  color: #f0f6fc;
  font-family: monospace;
}

.action-buttons-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 6px;
}

.trade-btn {
  padding: 12px;
  border-radius: 6px;
  border: none;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.2s;
}

.trade-btn.buy {
  background: #ef4444;
  color: #ffffff;
}

.trade-btn.buy:hover:not(:disabled) {
  background: #dc2626;
}

.trade-btn.sell {
  background: #10b981;
  color: #ffffff;
}

.trade-btn.sell:hover:not(:disabled) {
  background: #059669;
}

.trade-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 表格公用 */
.table-wrapper {
  overflow-x: auto;
}

.terminal-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.terminal-table th {
  background: #0d1117;
  color: #8b949e;
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid #21262d;
  font-weight: 600;
}

.terminal-table td {
  padding: 9px 10px;
  border-bottom: 1px solid #21262d;
  color: #c9d1d9;
}

.code-col {
  font-family: monospace;
  color: #58a6ff;
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

.sub-pnl {
  font-size: 10px;
  color: #8b949e;
}

.quick-actions {
  display: flex;
  gap: 6px;
}

.mini-btn {
  border: none;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}

.mini-btn.kline {
  background: #21262d;
  color: #58a6ff;
  border: 1px solid #30363d;
}
.mini-btn.kline:hover {
  border-color: #58a6ff;
}

.mini-btn.sell {
  background: rgba(16, 185, 129, 0.2);
  color: #10b981;
}

.mini-btn.sell:hover {
  background: rgba(16, 185, 129, 0.4);
}

.mini-btn.half {
  background: rgba(234, 179, 8, 0.2);
  color: #eab308;
}

.mini-btn.half:hover {
  background: rgba(234, 179, 8, 0.4);
}

.side-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
}

.side-badge.buy {
  background: rgba(239, 68, 68, 0.2);
  color: #ef4444;
}

.side-badge.sell {
  background: rgba(16, 185, 129, 0.2);
  color: #10b981;
}

.type-tag {
  font-size: 11px;
  background: #0d1117;
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid #30363d;
  color: #8b949e;
}

.status-done {
  color: #3fb950;
  font-size: 11px;
}

.time-col {
  font-family: monospace;
  font-size: 11px;
  color: #8b949e;
}

.empty-cell {
  text-align: center;
  padding: 30px;
  color: #8b949e;
}

.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid #30363d;
  border-top-color: #58a6ff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  display: inline-block;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
