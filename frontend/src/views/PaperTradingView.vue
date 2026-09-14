<template>
  <div class="view-container">
    <!-- 顶部主标题与双模式切换 -->
    <div class="view-toolbar">
      <div class="toolbar-left">
        <h2 class="view-title">⏳ AI 历史推演与时间加速沙盒</h2>
        <span class="view-subtitle">存入往期数据 · 多方案与自定义选股 · 时间加速推演 · 探究 AI 真实盈利水平与实盘胜率</span>
      </div>

      <div class="toolbar-right">
        <!-- 往期数据存入与检查状态 -->
        <button
          class="action-btn seed-btn"
          @click="handleSeedData"
          :disabled="seeding || isPlaying"
          title="检查并写入 2023-2024 年全量历史行情与基准日K线"
        >
          {{ seeding ? '正在存入往期数据...' : '📥 存入/重置往期历史数据' }}
        </button>

        <!-- 多方案同台竞技对比 -->
        <button
          class="action-btn compare-btn"
          @click="openCompareModal"
          :disabled="seeding || isPlaying"
          title="同时推演 4 种 AI 方案，同台对比净值与超额 Alpha"
        >
          🏁 AI 多方案竞技对比
        </button>
      </div>
    </div>

    <!-- 顶栏历史数据准备提示条 -->
    <div class="seed-status-bar" v-if="seedInfo">
      <span class="seed-icon">📊</span>
      <span class="seed-text">
        往期数据就绪：覆盖 <b>{{ seedInfo.stock_count || 20 }}</b> 只代表性标的与沪深300基准，
        区间 <b>{{ seedInfo.min_date }}</b> 至 <b>{{ seedInfo.max_date }}</b>，
        共 <b>{{ (seedInfo.total_bars || 10420).toLocaleString() }}</b> 根历史日 K 记录。
      </span>
    </div>

    <!-- 1. AI 选股方案配置卡片组 -->
    <div class="scheme-section">
      <div class="section-label-row">
        <span class="sec-badge">AI SCHEMES</span>
        <span class="sec-title">选择 AI 推演策略方案或自定义配置：</span>
      </div>

      <div class="schemes-grid">
        <!-- 方案 1: 强势动量 -->
        <div
          class="scheme-card"
          :class="{ active: currentScheme === 'momentum' }"
          @click="selectScheme('momentum')"
        >
          <div class="sc-header">
            <span class="sc-icon">🚀</span>
            <span class="sc-title">AI 强势动量突破</span>
            <span class="sc-tag momentum">高Alpha弹性</span>
          </div>
          <p class="sc-desc">专攻高综合分、多头突破爆发标的，单票上限 25%，止损 5%，止盈 15%，5日快速轮动。</p>
          <div class="sc-footer">
            <span>止损: <b>-5%</b></span>
            <span>止盈: <b>+15%</b></span>
            <span>持仓: <b>≤4只</b></span>
          </div>
        </div>

        <!-- 方案 2: 稳健价值 -->
        <div
          class="scheme-card"
          :class="{ active: currentScheme === 'value' }"
          @click="selectScheme('value')"
        >
          <div class="sc-header">
            <span class="sc-icon">🛡️</span>
            <span class="sc-title">AI 稳健低估值分红</span>
            <span class="sc-tag value">防御低回撤</span>
          </div>
          <p class="sc-desc">偏好黄金低价池 (≤¥20) 与低波动高股息资产，单票上限 15%，止损 7%，止盈 12%，稳健长跑。</p>
          <div class="sc-footer">
            <span>止损: <b>-7%</b></span>
            <span>止盈: <b>+12%</b></span>
            <span>持仓: <b>≤6只</b></span>
          </div>
        </div>

        <!-- 方案 3: 双引擎均衡 -->
        <div
          class="scheme-card"
          :class="{ active: currentScheme === 'balanced' }"
          @click="selectScheme('balanced')"
        >
          <div class="sc-header">
            <span class="sc-icon">⚖️</span>
            <span class="sc-title">AI 双引擎多因子均衡</span>
            <span class="sc-tag balanced">量化中枢</span>
          </div>
          <p class="sc-desc">LightGBM 机器学习与线性模型动态加权均衡配置，单票上限 20%，止损 6%，止盈 15%。</p>
          <div class="sc-footer">
            <span>止损: <b>-6%</b></span>
            <span>止盈: <b>+15%</b></span>
            <span>持仓: <b>≤5只</b></span>
          </div>
        </div>

        <!-- 方案 4: 自定义选股推演 -->
        <div
          class="scheme-card custom"
          :class="{ active: currentScheme === 'custom' }"
          @click="selectScheme('custom')"
        >
          <div class="sc-header">
            <span class="sc-icon">🛠️</span>
            <span class="sc-title">自定义选股推演方案</span>
            <span class="sc-tag custom">自由调参</span>
          </div>
          <p class="sc-desc">自由选择股票池（如仅自选股⭐）、单票仓位、止损止盈比率与调仓节奏，探究自定义收益。</p>
          <div class="sc-footer">
            <span>标的池: <b>{{ customForm.pool_filter === 'watchlist' ? '仅自选股⭐' : '全市场池' }}</b></span>
            <span>参数: <b>可展开配置</b></span>
          </div>
        </div>
      </div>

      <!-- 自定义方案专属展开面板 -->
      <div class="custom-config-panel" v-if="currentScheme === 'custom'">
        <div class="config-row">
          <div class="cfg-item">
            <label>股票池选择：</label>
            <select v-model="customForm.pool_filter" class="terminal-select">
              <option value="all">全市场代表性标的池 (20+只)</option>
              <option value="watchlist">⭐ 仅推演我的自选股 ({{ watchlistCodes.length }} 只)</option>
              <option value="low">🟢 仅黄金低价池 (≤¥20)</option>
            </select>
          </div>

          <div class="cfg-item">
            <label>初始本金：</label>
            <select v-model.number="customForm.initial_cash" class="terminal-select">
              <option :value="100000">10 万现金</option>
              <option :value="500000">50 万现金</option>
              <option :value="1000000">100 万现金 (标准)</option>
              <option :value="2000000">200 万现金</option>
            </select>
          </div>

          <div class="cfg-item">
            <label>单票仓位上限：</label>
            <select v-model.number="customForm.max_single_weight" class="terminal-select">
              <option :value="0.10">10% 仓位 (分散)</option>
              <option :value="0.20">20% 仓位 (标准)</option>
              <option :value="0.30">30% 仓位 (进取)</option>
              <option :value="0.40">40% 仓位 (集中)</option>
            </select>
          </div>

          <div class="cfg-item">
            <label>止损 / 止盈线：</label>
            <div class="dual-inputs">
              <select v-model.number="customForm.stop_loss" class="terminal-select mini">
                <option :value="0.03">-3% 止损</option>
                <option :value="0.05">-5% 止损</option>
                <option :value="0.07">-7% 止损</option>
                <option :value="0.10">-10% 止损</option>
              </select>
              <select v-model.number="customForm.take_profit" class="terminal-select mini">
                <option :value="0.08">+8% 止盈</option>
                <option :value="0.12">+12% 止盈</option>
                <option :value="0.15">+15% 止盈</option>
                <option :value="0.20">+20% 止盈</option>
              </select>
            </div>
          </div>

          <div class="cfg-item">
            <label>调仓轮动节奏：</label>
            <select v-model.number="customForm.rebalance_interval" class="terminal-select">
              <option :value="3">每 3 交易日</option>
              <option :value="5">每 5 交易日 (周频)</option>
              <option :value="10">每 10 交易日 (双周)</option>
              <option :value="20">每 20 交易日 (月频)</option>
            </select>
          </div>

          <button class="action-btn apply-cfg-btn" @click="handleInitSandbox">
            ⚡ 应用参数并初始化推演
          </button>
        </div>
      </div>
    </div>

    <!-- 2. 时间加速与播放控制台 -->
    <div class="time-control-card">
      <div class="tc-top">
        <div class="tc-date-info">
          <span class="tc-badge">TIME-TRAVEL CONTROLLER</span>
          <span class="tc-current-date">
            正在推演：<b>{{ sandbox.current_date || '--' }}</b>
          </span>
          <span class="tc-step-num">
            第 <b>{{ sandbox.current_step || 0 }}</b> / {{ sandbox.total_steps || 0 }} 交易日
          </span>
          <span class="tc-finish-badge" v-if="sandbox.is_finished">🏁 已完成全周期推演</span>
        </div>

        <div class="tc-speed-selector">
          <span class="speed-lbl">加速流速：</span>
          <button
            class="speed-btn"
            :class="{ active: playSpeed === 1 }"
            @click="setPlaySpeed(1)"
          >
            1x (1天/秒)
          </button>
          <button
            class="speed-btn"
            :class="{ active: playSpeed === 3 }"
            @click="setPlaySpeed(3)"
          >
            3x (快步)
          </button>
          <button
            class="speed-btn"
            :class="{ active: playSpeed === 10 }"
            @click="setPlaySpeed(10)"
          >
            10x (飞速)
          </button>
        </div>
      </div>

      <!-- 进度条 -->
      <div class="tc-progress-container">
        <div class="tc-progress-fill" :style="{ width: (sandbox.progress_pct || 0) + '%' }"></div>
      </div>

      <!-- 播放与单步控制按钮组 -->
      <div class="tc-buttons-row">
        <button
          class="tc-btn play-btn"
          v-if="!isPlaying"
          @click="startPlay"
          :disabled="sandbox.is_finished"
        >
          ▶ 开始时间加速推演
        </button>
        <button
          class="tc-btn pause-btn"
          v-else
          @click="pausePlay"
        >
          ⏸ 暂停推演
        </button>

        <button
          class="tc-btn step-btn"
          @click="stepDay(1)"
          :disabled="isPlaying || sandbox.is_finished"
        >
          ⏭ 单日步进 (+1天)
        </button>

        <button
          class="tc-btn step-btn"
          @click="stepDay(10)"
          :disabled="isPlaying || sandbox.is_finished"
        >
          ⏩ 快速推进 (+10天)
        </button>

        <button
          class="tc-btn instant-btn"
          @click="handleFastForward"
          :disabled="isPlaying || sandbox.is_finished"
          title="秒级直接完成全周期推演"
        >
          ⚡ 极速全周期秒级推演
        </button>

        <button
          class="tc-btn reset-btn"
          @click="handleInitSandbox"
          :disabled="isPlaying"
        >
          🔄 重置回到起点
        </button>
      </div>
    </div>

    <!-- 3. AI 盈利水平核心指标看板 (探究盈利能力) -->
    <div class="metrics-grid">
      <!-- 策略累计收益率 -->
      <div class="metric-card main">
        <span class="m-title">AI 策略累计收益率</span>
        <span class="m-val" :class="sandbox.metrics?.total_return_pct >= 0 ? 'text-up' : 'text-down'">
          {{ sandbox.metrics?.total_return_pct >= 0 ? '+' : '' }}{{ sandbox.metrics?.total_return_pct || '0.00' }}%
        </span>
        <span class="m-sub">
          总资产: ¥{{ Number(sandbox.total_equity || sandbox.initial_cash || 1000000).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}
        </span>
      </div>

      <!-- 沪深300基准收益率 -->
      <div class="metric-card">
        <span class="m-title">同期沪深300基准</span>
        <span class="m-val" :class="sandbox.metrics?.benchmark_return_pct >= 0 ? 'text-up' : 'text-down'">
          {{ sandbox.metrics?.benchmark_return_pct >= 0 ? '+' : '' }}{{ sandbox.metrics?.benchmark_return_pct || '0.00' }}%
        </span>
        <span class="m-sub">基准对照指数走势</span>
      </div>

      <!-- 超额 Alpha -->
      <div class="metric-card highlight">
        <span class="m-title">超额 Alpha 跑赢幅度</span>
        <span class="m-val" :class="sandbox.metrics?.alpha_pct >= 0 ? 'text-up' : 'text-down'">
          {{ sandbox.metrics?.alpha_pct >= 0 ? '+' : '' }}{{ sandbox.metrics?.alpha_pct || '0.00' }}%
        </span>
        <span class="m-sub">策略大幅跑赢大盘超额收益</span>
      </div>

      <!-- 最大回撤 -->
      <div class="metric-card">
        <span class="m-title">历史最大回撤 (MaxDD)</span>
        <span class="m-val text-down">
          -{{ sandbox.metrics?.max_drawdown_pct || '0.00' }}%
        </span>
        <span class="m-sub">峰值至谷底最大跌幅</span>
      </div>

      <!-- 实盘胜率 -->
      <div class="metric-card">
        <span class="m-title">AI 交易胜率</span>
        <span class="m-val text-warning">
          {{ sandbox.metrics?.win_rate_pct || '0.0' }}%
        </span>
        <span class="m-sub">已结算交易 {{ sandbox.metrics?.total_trades || 0 }} 笔</span>
      </div>

      <!-- 盈亏比与夏普 -->
      <div class="metric-card">
        <span class="m-title">盈亏比 / 年化夏普比率</span>
        <span class="m-val">
          {{ sandbox.metrics?.profit_loss_ratio || '1.00' }} / {{ sandbox.metrics?.sharpe_ratio || '0.00' }}
        </span>
        <span class="m-sub">胜率与风报比兼顾</span>
      </div>
    </div>

    <!-- 4. 动态资产净值曲线 vs 沪深300基准 (ECharts) -->
    <div class="chart-card">
      <div class="chart-card-header">
        <div class="cch-left">
          <span class="cch-title">📈 策略净值走势 vs 沪深300 基准 (实时动态生成)</span>
          <span class="cch-tip">实线为 AI 策略净值，虚线为同期大盘基准，随时间推进动态延伸</span>
        </div>
        <div class="cch-legend">
          <span class="leg-item"><span class="leg-color strategy"></span>AI 策略净值</span>
          <span class="leg-item"><span class="leg-color benchmark"></span>沪深300基准</span>
        </div>
      </div>
      <div ref="curveChartRef" class="equity-chart-box"></div>
    </div>

    <!-- 5. 推演中的动态持仓与调仓流水表 -->
    <div class="details-split-grid">
      <!-- 左侧：当前推演持仓 -->
      <div class="detail-box">
        <div class="detail-header">
          <h3 class="dh-title">💼 推演当前持仓 ({{ sandbox.positions?.length || 0 }} 只)</h3>
          <span class="dh-cash">可用现金: ¥{{ Number(sandbox.cash || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
        </div>
        <div class="detail-table-wrapper">
          <table class="terminal-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>名称</th>
                <th>股数</th>
                <th>买入成本</th>
                <th>当前价</th>
                <th>持仓市值</th>
                <th>浮动盈亏</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!sandbox.positions || sandbox.positions.length === 0">
                <td colspan="7" class="empty-row">当前持仓为空，时间推进后 AI 将自动选股建仓</td>
              </tr>
              <tr v-for="pos in sandbox.positions" :key="pos.code">
                <td><b>{{ pos.code }}</b></td>
                <td>{{ pos.name }}</td>
                <td>{{ pos.shares }} 股</td>
                <td>¥{{ Number(pos.avg_cost).toFixed(2) }}</td>
                <td>¥{{ Number(pos.current_price).toFixed(2) }}</td>
                <td>¥{{ Number(pos.market_value).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</td>
                <td :class="pos.pnl >= 0 ? 'text-up' : 'text-down'">
                  {{ pos.pnl >= 0 ? '+' : '' }}{{ pos.pnl_pct }}%
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 右侧：AI 调仓流水明细 -->
      <div class="detail-box">
        <div class="detail-header">
          <h3 class="dh-title">📜 AI 调仓与风控执行流水 (最近 30 笔)</h3>
          <span class="dh-tip">包含 AI 智能建仓、达标止盈与硬止损离场</span>
        </div>
        <div class="detail-table-wrapper">
          <table class="terminal-table">
            <thead>
              <tr>
                <th>日期</th>
                <th>标的</th>
                <th>动作</th>
                <th>价格</th>
                <th>股数</th>
                <th>盈亏</th>
                <th>触发原因</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!sandbox.trades || sandbox.trades.length === 0">
                <td colspan="7" class="empty-row">暂无调仓流水，启动时间加速推演后将实时更新</td>
              </tr>
              <tr v-for="(tr, idx) in sandbox.trades" :key="idx">
                <td class="time-cell">{{ tr.date }}</td>
                <td><b>{{ tr.name }}</b> ({{ tr.code }})</td>
                <td>
                  <span class="side-badge" :class="tr.side === '买入' ? 'buy' : 'sell'">
                    {{ tr.side }}
                  </span>
                </td>
                <td>¥{{ Number(tr.price).toFixed(2) }}</td>
                <td>{{ tr.shares }}</td>
                <td :class="tr.pnl >= 0 ? 'text-up' : 'text-down'">
                  <span v-if="tr.side === '卖出'">{{ tr.pnl >= 0 ? '+' : '' }}¥{{ tr.pnl }} ({{ tr.pnl_pct }}%)</span>
                  <span v-else class="text-muted">--</span>
                </td>
                <td class="reason-cell">{{ tr.reason }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 6. 多方案同台竞技对比弹窗 -->
    <div class="modal-backdrop" v-if="compareModalVisible" @click.self="compareModalVisible = false">
      <div class="compare-modal-card">
        <div class="cm-header">
          <h3 class="cm-title">🏁 AI 4 大方案全周期竞技对比</h3>
          <button class="close-btn" @click="compareModalVisible = false">✕</button>
        </div>

        <div class="cm-body">
          <div v-if="compareLoading" class="compare-loading">
            <span class="spinner"></span>
            <span>正在同时推演 4 种方案并测算全周期收益对比...</span>
          </div>

          <div v-else class="compare-content">
            <!-- 榜单对比表格 -->
            <div class="leaderboard-box">
              <table class="terminal-table">
                <thead>
                  <tr>
                    <th>方案名称</th>
                    <th>累计收益率</th>
                    <th>超额 Alpha</th>
                    <th>最大回撤</th>
                    <th>交易胜率</th>
                    <th>夏普比率</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(summary, skey) in compareData.summary" :key="skey">
                    <td><b>{{ summary.name }}</b></td>
                    <td :class="summary.total_return_pct >= 0 ? 'text-up' : 'text-down'">
                      {{ summary.total_return_pct >= 0 ? '+' : '' }}{{ summary.total_return_pct }}%
                    </td>
                    <td :class="summary.alpha_pct >= 0 ? 'text-up' : 'text-down'">
                      {{ summary.alpha_pct >= 0 ? '+' : '' }}{{ summary.alpha_pct }}%
                    </td>
                    <td class="text-down">-{{ summary.max_drawdown_pct }}%</td>
                    <td class="text-warning">{{ summary.win_rate_pct }}%</td>
                    <td><b>{{ summary.sharpe_ratio }}</b></td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- 对比走势图 -->
            <div ref="compareChartRef" class="compare-chart-canvas"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- K 线查看弹窗 -->
    <KLineModal
      v-model:visible="klineVisible"
      :stock="selectedStockForKline"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api'
import KLineModal from '../components/KLineModal.vue'

const seeding = ref(false)
const seedInfo = ref(null)

const currentScheme = ref('momentum') // 'momentum' | 'value' | 'balanced' | 'custom'
const playSpeed = ref(3) // 1, 3, 10
const isPlaying = ref(false)
let playTimer = null

const sandbox = ref({
  current_date: '',
  current_step: 0,
  total_steps: 0,
  progress_pct: 0,
  is_finished: false,
  cash: 1000000,
  total_equity: 1000000,
  positions: [],
  equity_curve: [],
  trades: [],
  metrics: {
    total_return_pct: 0,
    benchmark_return_pct: 0,
    alpha_pct: 0,
    max_drawdown_pct: 0,
    win_rate_pct: 0,
    profit_loss_ratio: 1,
    total_trades: 0,
    sharpe_ratio: 0,
  }
})

const watchlistCodes = ref([])

const customForm = reactive({
  pool_filter: 'all',
  initial_cash: 1000000,
  max_single_weight: 0.20,
  stop_loss: 0.05,
  take_profit: 0.15,
  rebalance_interval: 5,
  max_positions: 5,
})

const curveChartRef = ref(null)
let curveChartInstance = null

const compareModalVisible = ref(false)
const compareLoading = ref(false)
const compareData = ref({ summary: {}, curves: {}, benchmark_curve: [] })
const compareChartRef = ref(null)
let compareChartInstance = null

const klineVisible = ref(false)
const selectedStockForKline = ref(null)

function selectScheme(s) {
  if (isPlaying.value) pausePlay()
  currentScheme.value = s
  handleInitSandbox()
}

function setPlaySpeed(sp) {
  playSpeed.value = sp
  if (isPlaying.value) {
    pausePlay()
    startPlay()
  }
}

async function handleSeedData() {
  seeding.value = true
  try {
    const res = await api.seedSandboxData(true)
    seedInfo.value = res
    await handleInitSandbox()
  } catch (err) {
    console.error('Failed to seed data:', err)
  } finally {
    seeding.value = false
  }
}

async function handleInitSandbox() {
  pausePlay()
  try {
    const params = {
      scheme: currentScheme.value,
      start_date: '2023-01-03',
      end_date: '2024-12-31',
      initial_cash: currentScheme.value === 'custom' ? customForm.initial_cash : 1000000,
      custom_config: currentScheme.value === 'custom' ? { ...customForm } : null,
      watchlist_codes: watchlistCodes.value,
    }
    const state = await api.initSandbox(params)
    sandbox.value = state
    updateCurveChart(state.equity_curve)
  } catch (err) {
    console.error('Failed to init sandbox:', err)
  }
}

async function stepDay(days = 1) {
  try {
    const state = await api.stepSandbox(days)
    sandbox.value = state
    updateCurveChart(state.equity_curve)
    if (state.is_finished) {
      pausePlay()
    }
  } catch (err) {
    console.error('Failed to step sandbox:', err)
    pausePlay()
  }
}

function startPlay() {
  if (sandbox.value.is_finished) return
  isPlaying.value = true
  const intervalMs = playSpeed.value === 1 ? 1000 : (playSpeed.value === 3 ? 350 : 100)
  playTimer = setInterval(() => {
    stepDay(1)
  }, intervalMs)
}

function pausePlay() {
  isPlaying.value = false
  if (playTimer) {
    clearInterval(playTimer)
    playTimer = null
  }
}

async function handleFastForward() {
  pausePlay()
  try {
    const state = await api.fastForwardSandbox()
    sandbox.value = state
    updateCurveChart(state.equity_curve)
  } catch (err) {
    console.error('Failed to fast forward:', err)
  }
}

function updateCurveChart(curve) {
  if (!curveChartRef.value) return
  if (!curveChartInstance) {
    curveChartInstance = echarts.init(curveChartRef.value, 'dark')
  }

  const dates = (curve || []).map(p => p.date)
  const strategyRet = (curve || []).map(p => p.return_pct)
  const benchRet = (curve || []).map(p => p.benchmark_return_pct)

  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#161b22',
      borderColor: '#30363d',
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      formatter: (params) => {
        if (!params.length) return ''
        let res = `<div style="font-weight:700;margin-bottom:4px">${params[0].axisValue}</div>`
        params.forEach(p => {
          const color = p.color
          res += `<div><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:6px"></span>`
          res += `${p.seriesName}: <b>${p.value >= 0 ? '+' : ''}${p.value}%</b></div>`
        })
        return res
      }
    },
    grid: {
      left: '4%',
      right: '3%',
      top: '12%',
      bottom: '10%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: '#30363d' } },
      axisLabel: { color: '#8b949e', fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#8b949e',
        formatter: '{value}%'
      },
      splitLine: { lineStyle: { color: '#161e2e', type: 'dashed' } }
    },
    series: [
      {
        name: 'AI 策略收益率',
        type: 'line',
        data: strategyRet,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#58a6ff', width: 2.5 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(88, 166, 255, 0.35)' },
            { offset: 1, color: 'rgba(88, 166, 255, 0.02)' }
          ])
        }
      },
      {
        name: '沪深300基准收益率',
        type: 'line',
        data: benchRet,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#eab308', width: 2, type: 'dashed' }
      }
    ]
  }

  curveChartInstance.setOption(option, true)
}

async function openCompareModal() {
  compareModalVisible.value = true
  compareLoading.value = true
  try {
    const res = await api.compareSandboxSchemes({
      start_date: '2023-01-03',
      end_date: '2024-12-31',
      initial_cash: 1000000,
      custom_config: { ...customForm },
      watchlist_codes: watchlistCodes.value,
    })
    compareData.value = res
    await nextTick()
    renderCompareChart(res)
  } catch (err) {
    console.error('Failed to compare schemes:', err)
  } finally {
    compareLoading.value = false
  }
}

function renderCompareChart(data) {
  if (!compareChartRef.value) return
  if (!compareChartInstance) {
    compareChartInstance = echarts.init(compareChartRef.value, 'dark')
  }

  const curves = data.curves || {}
  const bench = data.benchmark_curve || []
  const dates = bench.map(p => p.date)

  const series = []
  const colors = {
    momentum: '#ff4d4f',
    value: '#2ea043',
    balanced: '#58a6ff',
    custom: '#a855f7'
  }
  const names = {
    momentum: 'AI 强势动量',
    value: 'AI 稳健价值',
    balanced: 'AI 双引擎均衡',
    custom: '自定义方案'
  }

  for (const [k, pts] of Object.entries(curves)) {
    series.push({
      name: names[k] || k,
      type: 'line',
      data: pts.map(p => p.return_pct),
      smooth: true,
      showSymbol: false,
      lineStyle: { color: colors[k] || '#c9d1d9', width: 2.2 }
    })
  }

  series.push({
    name: '沪深300基准',
    type: 'line',
    data: bench.map(p => p.return_pct),
    smooth: true,
    showSymbol: false,
    lineStyle: { color: '#eab308', width: 2, type: 'dashed' }
  })

  const option = {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#c9d1d9' } },
    grid: { left: '4%', right: '3%', top: '15%', bottom: '10%', containLabel: true },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#8b949e' } },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}%', color: '#8b949e' } },
    series
  }

  compareChartInstance.setOption(option, true)
}

function handleResize() {
  if (curveChartInstance) curveChartInstance.resize()
  if (compareChartInstance) compareChartInstance.resize()
}

onMounted(async () => {
  window.addEventListener('resize', handleResize)
  // 获取自选股
  try {
    const wl = await api.getWatchlist()
    if (wl) watchlistCodes.value = wl.map(s => s.code)
  } catch (err) {
    console.debug('Failed to get watchlist:', err)
  }

  // 检查种子状态
  try {
    const seedRes = await api.seedSandboxData(false)
    seedInfo.value = seedRes
  } catch (err) {
    console.debug('Seed check:', err)
  }

  // 初始化沙盒
  await handleInitSandbox()
})

onUnmounted(() => {
  pausePlay()
  window.removeEventListener('resize', handleResize)
  if (curveChartInstance) curveChartInstance.dispose()
  if (compareChartInstance) compareChartInstance.dispose()
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
  flex-wrap: wrap;
  gap: 12px;
}

.view-title {
  font-size: 20px;
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
  gap: 10px;
}

.action-btn {
  border: 1px solid #30363d;
  padding: 7px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.seed-btn {
  background: #1c2738;
  border-color: #2b3d5b;
  color: #58a6ff;
}

.seed-btn:hover:not(:disabled) {
  background: #2b3d5b;
}

.compare-btn {
  background: #21262d;
  border-color: #30363d;
  color: #f0f6fc;
}

.compare-btn:hover:not(:disabled) {
  border-color: #58a6ff;
  color: #58a6ff;
}

/* 状态提示条 */
.seed-status-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 16px;
  background: rgba(88, 166, 255, 0.08);
  border: 1px solid rgba(88, 166, 255, 0.2);
  border-radius: 6px;
  font-size: 12px;
  color: #c9d1d9;
}

.seed-status-bar b {
  color: #58a6ff;
}

/* 方案卡片 */
.scheme-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-label-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sec-badge {
  background: #238636;
  color: #ffffff;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}

.sec-title {
  font-size: 14px;
  font-weight: 600;
  color: #f0f6fc;
}

.schemes-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.scheme-card {
  background: #121824;
  border: 1px solid #283347;
  border-radius: 8px;
  padding: 14px 16px;
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.scheme-card:hover {
  border-color: #3b82f6;
  transform: translateY(-2px);
}

.scheme-card.active {
  border-color: #58a6ff;
  background: #162033;
  box-shadow: 0 0 12px rgba(88, 166, 255, 0.2);
}

.sc-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sc-title {
  font-size: 14px;
  font-weight: 700;
  color: #f0f6fc;
  flex: 1;
}

.sc-tag {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 4px;
}

.sc-tag.momentum { background: rgba(255, 77, 79, 0.2); color: #ff4d4f; }
.sc-tag.value { background: rgba(46, 160, 67, 0.2); color: #2ea043; }
.sc-tag.balanced { background: rgba(88, 166, 255, 0.2); color: #58a6ff; }
.sc-tag.custom { background: rgba(168, 85, 247, 0.2); color: #a855f7; }

.sc-desc {
  font-size: 12px;
  color: #8b949e;
  line-height: 1.5;
  margin: 0;
  flex: 1;
}

.sc-footer {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #8b949e;
  border-top: 1px solid #1c2738;
  padding-top: 6px;
}

.sc-footer b {
  color: #e2e8f0;
}

/* 自定义方案调参面板 */
.custom-config-panel {
  background: #101622;
  border: 1px solid #3b82f6;
  border-radius: 8px;
  padding: 14px 18px;
}

.config-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
}

.cfg-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cfg-item label {
  font-size: 11px;
  color: #8b949e;
  font-weight: 600;
}

.terminal-select {
  background: #161b22;
  border: 1px solid #30363d;
  color: #f0f6fc;
  padding: 5px 10px;
  border-radius: 6px;
  font-size: 12px;
  outline: none;
}

.terminal-select.mini {
  width: 95px;
}

.dual-inputs {
  display: flex;
  gap: 6px;
}

.apply-cfg-btn {
  margin-top: 16px;
  background: #238636;
  color: #fff;
  border: none;
  align-self: flex-end;
}

/* 时间控制台 */
.time-control-card {
  background: #121824;
  border: 1px solid #283347;
  border-radius: 8px;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tc-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.tc-date-info {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.tc-badge {
  background: #3b82f6;
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}

.tc-current-date {
  font-size: 16px;
  color: #f0f6fc;
}

.tc-current-date b {
  color: #58a6ff;
  font-family: monospace;
}

.tc-step-num {
  font-size: 12px;
  color: #8b949e;
}

.tc-finish-badge {
  font-size: 11px;
  color: #2ea043;
  font-weight: 700;
  background: rgba(46, 160, 67, 0.15);
  padding: 2px 8px;
  border-radius: 4px;
}

.tc-speed-selector {
  display: flex;
  align-items: center;
  gap: 6px;
}

.speed-lbl {
  font-size: 12px;
  color: #8b949e;
}

.speed-btn {
  background: #161b22;
  border: 1px solid #30363d;
  color: #8b949e;
  padding: 4px 10px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.speed-btn.active {
  background: #238636;
  color: #ffffff;
  border-color: #2ea043;
}

.tc-progress-container {
  height: 6px;
  background: #161b22;
  border-radius: 3px;
  overflow: hidden;
}

.tc-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #00e676);
  transition: width 0.1s linear;
}

.tc-buttons-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.tc-btn {
  padding: 7px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.2s;
}

.play-btn {
  background: #238636;
  color: #ffffff;
}

.play-btn:hover:not(:disabled) {
  background: #2ea043;
}

.pause-btn {
  background: #d29922;
  color: #161b22;
}

.step-btn {
  background: #21262d;
  color: #c9d1d9;
  border-color: #30363d;
}

.step-btn:hover:not(:disabled) {
  border-color: #58a6ff;
  color: #58a6ff;
}

.instant-btn {
  background: #3b82f6;
  color: #ffffff;
}

.instant-btn:hover:not(:disabled) {
  background: #2563eb;
}

.reset-btn {
  background: #21262d;
  color: #8b949e;
  border-color: #30363d;
}

/* 核心盈利指标卡片 */
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.metric-card {
  background: #121824;
  border: 1px solid #283347;
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.metric-card.main {
  background: #131c2e;
  border-color: #3b82f6;
}

.metric-card.highlight {
  background: #182218;
  border-color: #2ea043;
}

.m-title {
  font-size: 11px;
  color: #8b949e;
  font-weight: 600;
}

.m-val {
  font-size: 22px;
  font-weight: 700;
  font-family: monospace;
  color: #f0f6fc;
}

.m-sub {
  font-size: 11px;
  color: #8b949e;
}

.text-up { color: #ff4d4f; }
.text-down { color: #00e676; }
.text-warning { color: #eab308; }

/* 图表卡片 */
.chart-card {
  background: #121824;
  border: 1px solid #283347;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chart-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.cch-title {
  font-size: 14px;
  font-weight: 700;
  color: #f0f6fc;
}

.cch-tip {
  font-size: 12px;
  color: #8b949e;
  margin-left: 10px;
}

.cch-legend {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: #c9d1d9;
}

.leg-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.leg-color {
  width: 12px;
  height: 4px;
  border-radius: 2px;
}

.leg-color.strategy { background: #58a6ff; }
.leg-color.benchmark { background: #eab308; }

.equity-chart-box {
  width: 100%;
  height: 320px;
}

/* 明细表格 */
.details-split-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

@media (max-width: 1024px) {
  .details-split-grid {
    grid-template-columns: 1fr;
  }
}

.detail-box {
  background: #121824;
  border: 1px solid #283347;
  border-radius: 8px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.dh-title {
  font-size: 14px;
  font-weight: 700;
  color: #f0f6fc;
  margin: 0;
}

.dh-cash {
  font-size: 12px;
  color: #58a6ff;
  font-weight: 600;
}

.dh-tip {
  font-size: 11px;
  color: #8b949e;
}

.detail-table-wrapper {
  max-height: 300px;
  overflow-y: auto;
}

.terminal-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.terminal-table th {
  background: #0d121c;
  color: #8b949e;
  text-align: left;
  padding: 8px 10px;
  font-weight: 600;
  border-bottom: 1px solid #21262d;
  position: sticky;
  top: 0;
}

.terminal-table td {
  padding: 7px 10px;
  border-bottom: 1px solid #1c2738;
  color: #c9d1d9;
}

.empty-row {
  text-align: center;
  padding: 30px !important;
  color: #8b949e;
}

.side-badge {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}

.side-badge.buy {
  background: rgba(255, 77, 79, 0.2);
  color: #ff4d4f;
}

.side-badge.sell {
  background: rgba(0, 230, 118, 0.2);
  color: #00e676;
}

.reason-cell {
  font-size: 11px;
  color: #8b949e;
}

.time-cell {
  font-family: monospace;
  font-size: 11px;
}

/* 对比弹窗 */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.8);
  backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.compare-modal-card {
  width: 900px;
  max-width: 94vw;
  height: 600px;
  background: #0e1420;
  border: 1px solid #2b3d5b;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.9);
}

.cm-header {
  height: 50px;
  background: #141c2c;
  border-bottom: 1px solid #212d40;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}

.cm-title {
  font-size: 16px;
  font-weight: 700;
  color: #f0f6fc;
}

.close-btn {
  background: transparent;
  border: none;
  color: #8b949e;
  font-size: 18px;
  cursor: pointer;
}

.cm-body {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.compare-loading {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #8b949e;
}

.spinner {
  width: 30px;
  height: 30px;
  border: 3px solid #30363d;
  border-top-color: #58a6ff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.compare-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
}

.leaderboard-box {
  background: #121824;
  border: 1px solid #212d40;
  border-radius: 8px;
  overflow: hidden;
}

.compare-chart-canvas {
  width: 100%;
  height: 320px;
}
</style>
