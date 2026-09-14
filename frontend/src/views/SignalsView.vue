<template>
  <div class="view-container">
    <!-- 顶部工具栏与检索 -->
    <div class="view-toolbar">
      <div class="toolbar-left">
        <h2 class="view-title">⚡ 智能选股推荐中心</h2>
        <span class="view-subtitle">基于双引擎（LightGBM + 线性模型）加权胜率，结合股票价格区间与一手买入资金门槛科学选股</span>
      </div>
      <div class="toolbar-right">
        <div class="search-box">
          <input
            v-model="searchQuery"
            type="text"
            placeholder="搜索代码 / 名称..."
            class="terminal-input"
          />
        </div>
        <button class="action-btn" @click="loadSignals" :disabled="loading">
          {{ loading ? '加载中...' : '🔄 刷新榜单' }}
        </button>
      </div>
    </div>

    <!-- 模式切换：价格区间选股推荐 VS 综合选股推荐 -->
    <div class="mode-tabs-bar">
      <div class="tabs-group">
        <button
          class="mode-tab-btn"
          :class="{ active: viewMode === 'bracket' }"
          @click="switchViewMode('bracket')"
        >
          🎯 价格区间选股推荐
          <span class="mode-desc">按单价区间/一手资金门槛分类精选</span>
        </button>
        <button
          class="mode-tab-btn"
          :class="{ active: viewMode === 'comprehensive' }"
          @click="switchViewMode('comprehensive')"
        >
          🌟 综合选股推荐
          <span class="mode-desc">全市场多因子胜率综合排行</span>
        </button>
        <button
          class="mode-tab-btn"
          :class="{ active: viewMode === 'watchlist' }"
          @click="switchViewMode('watchlist')"
        >
          ⭐ 我的自选股 ({{ watchlistCodes.length }})
          <span class="mode-desc">自选关注标的与多因子跟踪</span>
        </button>
      </div>

      <div class="sort-controls" v-if="viewMode === 'comprehensive' || viewMode === 'watchlist'">
        <label class="sort-lbl">排序方式：</label>
        <select v-model="sortBy" class="terminal-select" @change="loadSignals">
          <option value="score">综合胜率得分降序 (默认)</option>
          <option value="hand_cost_asc">一手门槛从低到高 (最亲民优先)</option>
          <option value="price_asc">股价从低到高</option>
          <option value="price_desc">股价从高到低</option>
        </select>
      </div>
    </div>

    <!-- 模式三专属：自选股管理与快速添加栏 -->
    <div class="watchlist-toolbar-section" v-if="viewMode === 'watchlist'">
      <div class="watchlist-info-bar">
        <div class="wl-left">
          <span class="wl-title">⭐ 我的自选股监控池</span>
          <span class="wl-count">已关注 <b>{{ watchlistCodes.length }}</b> 只标的</span>
          <span class="wl-tip">点击表格中的 ☆ / ⭐ 可快速添加或移除自选</span>
        </div>
        <div class="wl-add-box">
          <input
            v-model="quickAddInput"
            type="text"
            placeholder="输入代码/名称(如 000001 / payh)..."
            class="terminal-input wl-input"
            @keyup.enter="handleQuickAddWatchlist"
          />
          <button class="action-btn wl-btn" @click="handleQuickAddWatchlist">
            ➕ 加自选
          </button>
        </div>
      </div>
    </div>

    <!-- 模式一专属：价格区间分类卡片与自定义价格滑块 -->
    <div class="bracket-filter-section" v-if="viewMode === 'bracket'">
      <div class="bracket-cards-grid">
        <!-- 全部区间 -->
        <div
          class="bracket-card"
          :class="{ active: selectedBracket === 'all' }"
          @click="selectBracket('all')"
        >
          <div class="bc-top">
            <span class="bc-name">全部价格带</span>
            <span class="bc-count">{{ stats.total_count || signals.length }} 只</span>
          </div>
          <div class="bc-desc">全价位标的池</div>
          <div class="bc-hand">涵盖全部单价梯度</div>
        </div>

        <!-- 1. 黄金低价池 (≤¥20) -->
        <div
          class="bracket-card low"
          :class="{ active: selectedBracket === 'low' }"
          @click="selectBracket('low')"
        >
          <div class="bc-top">
            <span class="bc-name">🟢 黄金低价池 (≤¥20)</span>
            <span class="bc-count">{{ stats.brackets?.low?.count || 0 }} 只</span>
          </div>
          <div class="bc-desc">低门槛、高弹性、适合中小资金分批布局</div>
          <div class="bc-hand highlight">
            一手仅需 <b>¥{{ stats.brackets?.low?.min_hand_cost || 425 }}</b> 起
          </div>
        </div>

        <!-- 2. 稳健中价池 (¥20~¥50) -->
        <div
          class="bracket-card mid"
          :class="{ active: selectedBracket === 'mid' }"
          @click="selectBracket('mid')"
        >
          <div class="bc-top">
            <span class="bc-name">🔵 稳健中价池 (¥20~¥50)</span>
            <span class="bc-count">{{ stats.brackets?.mid?.count || 0 }} 只</span>
          </div>
          <div class="bc-desc">绩优蓝筹白马，行业中枢，稳健增长</div>
          <div class="bc-hand">
            一手约 <b>¥{{ stats.brackets?.mid?.min_hand_cost || 2850 }}</b> 起
          </div>
        </div>

        <!-- 3. 成长中高价池 (¥50~¥100) -->
        <div
          class="bracket-card high"
          :class="{ active: selectedBracket === 'high' }"
          @click="selectBracket('high')"
        >
          <div class="bc-top">
            <span class="bc-name">🟣 成长中高价 (¥50~¥100)</span>
            <span class="bc-count">{{ stats.brackets?.high?.count || 0 }} 只</span>
          </div>
          <div class="bc-desc">高景气赛道成长股、医药/新能源高端制造</div>
          <div class="bc-hand">
            一手约 <b>¥{{ stats.brackets?.high?.min_hand_cost || 5200 }}</b> 起
          </div>
        </div>

        <!-- 4. 百元核心资产 (>¥100) -->
        <div
          class="bracket-card top"
          :class="{ active: selectedBracket === 'top' }"
          @click="selectBracket('top')"
        >
          <div class="bc-top">
            <span class="bc-name">🔴 百元核心资产 (>¥100)</span>
            <span class="bc-count">{{ stats.brackets?.top?.count || 0 }} 只</span>
          </div>
          <div class="bc-desc">高权重核心资产，适合大资金配置</div>
          <div class="bc-hand">
            一手 <b>> ¥10,000</b>
          </div>
        </div>
      </div>

      <!-- 自定义价格区间输入 -->
      <div class="custom-range-bar">
        <span class="cr-label">🎚️ 自定义价格区间筛选：</span>
        <div class="range-inputs">
          <input
            v-model.number="customMinPrice"
            type="number"
            step="0.5"
            placeholder="最低价 ¥"
            class="range-input"
            @input="onCustomPriceChange"
          />
          <span class="range-sep">至</span>
          <input
            v-model.number="customMaxPrice"
            type="number"
            step="0.5"
            placeholder="最高价 ¥"
            class="range-input"
            @input="onCustomPriceChange"
          />
          <button class="reset-range-btn" @click="resetCustomRange" v-if="customMinPrice || customMaxPrice">
            重置区间
          </button>
        </div>
        <span class="cr-tip" v-if="customMinPrice || customMaxPrice">
          当前自定义区间: ¥{{ customMinPrice || 0 }} ~ ¥{{ customMaxPrice || '无上限' }}
        </span>
      </div>
    </div>

    <!-- 信号统计指标看板 -->
    <div class="stats-row">
      <div class="stat-card">
        <span class="stat-label">当前展示标的</span>
        <span class="stat-val">{{ displaySignals.length }} 只</span>
      </div>
      <div class="stat-card highlight">
        <span class="stat-label">首推精选 (TOP 1)</span>
        <span class="stat-val">{{ topSignal ? `${topSignal.name} (${topSignal.code})` : '--' }}</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">首推现价与一手门槛</span>
        <span class="stat-val score" v-if="topSignal">
          ¥{{ topSignal.price?.toFixed(2) }} (¥{{ topSignal.hand_cost?.toLocaleString() }}/手)
        </span>
        <span class="stat-val score" v-else>--</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">当前最亲民标的</span>
        <span class="stat-val cheapest" v-if="cheapestSignal">
          {{ cheapestSignal.name }} (¥{{ cheapestSignal.price }}/股 · 1手仅¥{{ cheapestSignal.hand_cost }})
        </span>
        <span class="stat-val cheapest" v-else>--</span>
      </div>
    </div>

    <!-- 推荐标的列表控制栏 -->
    <div class="table-header-bar">
      <div class="th-left">
        <span class="th-title">📋 推荐标的列表</span>
        <span class="th-count">
          当前展示 <b>{{ paginatedSignals.length }}</b> / 共 <b>{{ displaySignals.length }}</b> 只
        </span>
        <span class="th-drag-hint">🖐️ 支持鼠标按住表格任意空白处直接上下拖拽滑动</span>
      </div>
      <div class="th-right">
        <span class="page-size-lbl">单页展示：</span>
        <div class="page-size-btns">
          <button
            class="ps-btn"
            :class="{ active: pageSize === 10 }"
            @click="setPageSize(10)"
          >
            10只
          </button>
          <button
            class="ps-btn"
            :class="{ active: pageSize === 20 }"
            @click="setPageSize(20)"
          >
            20只
          </button>
          <button
            class="ps-btn"
            :class="{ active: pageSize >= 999 }"
            @click="setPageSize(999)"
          >
            全部36只
          </button>
        </div>
      </div>
    </div>

    <!-- 信号数据列表表格 (支持拖拽滚动) -->
    <div
      ref="tableWrapperRef"
      class="table-wrapper"
      :class="{ 'is-dragging': isDragging }"
      @mousedown="onTableMouseDown"
    >
      <table class="terminal-table">
        <thead>
          <tr>
            <th width="60">排名</th>
            <th width="100">代码</th>
            <th width="120">名称</th>
            <th width="110">最新价格</th>
            <th width="140">一手资金门槛 (100股)</th>
            <th width="130">价格区间定位</th>
            <th width="120">综合胜率分</th>
            <th width="100">LightGBM</th>
            <th width="100">多因子</th>
            <th width="230">深度分析与模拟建仓</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="displaySignals.length === 0">
            <td colspan="10" class="empty-cell">
              {{ loading ? '正在计算并筛选推荐标的...' : '当前筛选条件下暂无符合条件的标的，请调整价格区间或清空搜索词。' }}
            </td>
          </tr>
          <tr
            v-for="(item, idx) in paginatedSignals"
            :key="item.code"
            class="clickable-row"
            @click="onRowClick(item)"
          >
            <td class="rank-col">
              <span class="rank-badge" :class="'rank-' + getGlobalRank(idx)">{{ getGlobalRank(idx) }}</span>
            </td>
            <td class="code-col"><b>{{ item.code }}</b></td>
            <td class="name-col"><b>{{ item.name || '--' }}</b></td>
            <td class="price-col">
              <span v-if="item.price > 0" class="price-val">¥{{ Number(item.price).toFixed(2) }}</span>
              <span v-else class="text-muted">--</span>
            </td>
            <!-- 一手资金门槛 -->
            <td class="hand-col">
              <div class="hand-cost-box">
                <span class="hand-val">¥{{ Number(item.hand_cost || item.price * 100).toLocaleString() }}</span>
                <span class="hand-badge" :class="getHandBadgeClass(item.price)">
                  {{ getHandBadgeLabel(item.price) }}
                </span>
              </div>
            </td>
            <!-- 价格区间定位 -->
            <td>
              <span class="bracket-tag" :class="item.price_bracket || 'low'">
                {{ item.bracket_label || '平价优质' }}
              </span>
            </td>
            <!-- 综合得分 -->
            <td>
              <div class="score-bar-container">
                <div
                  class="score-fill"
                  :style="{ width: Math.min(100, Math.max(10, (item.score_blend || 0) * 100)) + '%' }"
                ></div>
                <span class="score-text">{{ item.score_blend?.toFixed(3) || '--' }}</span>
              </div>
            </td>
            <td class="score-secondary">{{ item.score_lgb?.toFixed(3) || '--' }}</td>
            <td class="score-secondary">{{ item.score_linear?.toFixed(3) || '--' }}</td>
            <!-- 操作列 -->
            <td>
              <div class="actions-group">
                <button
                  class="action-mini star-btn"
                  :class="{ favorited: isFavorited(item.code) }"
                  @click.stop="toggleWatchlist(item)"
                  :title="isFavorited(item.code) ? '点击移出自选' : '点击加入自选'"
                >
                  {{ isFavorited(item.code) ? '⭐ 已自选' : '☆ 加自选' }}
                </button>
                <button class="action-mini why" @click.stop="openAttribution(item)" title="查看多因子深度归因">
                  🔍 为什么推荐
                </button>
                <button class="action-mini kline" @click.stop="openKline(item)" title="查看高清蜡烛图">
                  📈 K线
                </button>
                <button class="action-mini paper" @click.stop="handleGoPaper(item)" title="直接带入模拟交易下单">
                  🎮 下单
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 底部翻页 / 全量展示与回顶工具栏 -->
    <div class="table-footer-bar">
      <div class="tf-left">
        <span class="tf-stat">
          第 <b>{{ currentPage }}</b> / <b>{{ totalPages }}</b> 页 · 共 <b>{{ displaySignals.length }}</b> 只精选标的
        </span>
      </div>
      <div class="tf-right" v-if="totalPages > 1">
        <button
          class="page-nav-btn"
          :disabled="currentPage <= 1"
          @click="changePage(currentPage - 1)"
        >
          ◀ 上一页
        </button>
        <button
          v-for="p in totalPages"
          :key="p"
          class="page-nav-btn page-num"
          :class="{ active: currentPage === p }"
          @click="changePage(p)"
        >
          {{ p }}
        </button>
        <button
          class="page-nav-btn"
          :disabled="currentPage >= totalPages"
          @click="changePage(currentPage + 1)"
        >
          下一页 ▶
        </button>
      </div>
      <div class="tf-right" v-else>
        <span class="all-shown-tip">✅ 当前已全量展示全部 {{ displaySignals.length }} 只推荐标的</span>
        <button class="scroll-top-btn" @click="scrollToTop">⬆ 回到顶部</button>
      </div>
    </div>

    <!-- 为什么推荐·多维归因弹窗 -->
    <StockAttributionModal
      v-model:visible="attributionVisible"
      :stock="selectedStock"
      @open-kline="openKline"
      @go-paper="handleGoPaper"
    />

    <!-- 高清 K 线弹窗 -->
    <KLineModal
      v-model:visible="klineVisible"
      :stock="selectedStock"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { api } from '../api'
import KLineModal from '../components/KLineModal.vue'
import StockAttributionModal from '../components/StockAttributionModal.vue'

const emit = defineEmits(['switch-tab'])

const viewMode = ref('bracket') // 'bracket' | 'comprehensive' | 'watchlist'
const selectedBracket = ref('all') // 'all', 'low', 'mid', 'high', 'top'
const sortBy = ref('score')
const customMinPrice = ref(null)
const customMaxPrice = ref(null)

const signals = ref([])
const watchlist = ref([])
const watchlistCodes = computed(() => watchlist.value.map(s => s.code))
const quickAddInput = ref('')

const stats = ref({ total_count: 0, brackets: {} })
const loading = ref(false)
const searchQuery = ref('')
const klineVisible = ref(false)
const attributionVisible = ref(false)
const selectedStock = ref(null)

function isFavorited(code) {
  return watchlistCodes.value.includes(code)
}

async function toggleWatchlist(stock) {
  const code = stock.code
  if (isFavorited(code)) {
    try {
      await api.removeFromWatchlist(code)
      watchlist.value = watchlist.value.filter(s => s.code !== code)
    } catch (err) {
      console.error('Failed to remove from watchlist:', err)
    }
  } else {
    try {
      await api.addToWatchlist({ code: stock.code, name: stock.name })
      watchlist.value.unshift({ ...stock, is_watchlist: true })
    } catch (err) {
      console.error('Failed to add to watchlist:', err)
    }
  }
}

async function handleQuickAddWatchlist() {
  const q = quickAddInput.value.trim()
  if (!q) return
  try {
    const searchRes = await api.searchStocks(q)
    if (searchRes && searchRes.length > 0) {
      const top = searchRes[0]
      await api.addToWatchlist({ code: top.code, name: top.name })
      quickAddInput.value = ''
      await loadWatchlist()
    } else {
      await api.addToWatchlist({ code: q, name: q })
      quickAddInput.value = ''
      await loadWatchlist()
    }
  } catch (err) {
    console.error('Failed to quick add watchlist:', err)
  }
}

async function loadWatchlist() {
  try {
    const res = await api.getWatchlist()
    if (res) watchlist.value = res
  } catch (err) {
    console.error('Failed to load watchlist:', err)
  }
}

function switchViewMode(mode) {
  viewMode.value = mode
  if (mode === 'comprehensive') {
    selectedBracket.value = 'all'
    customMinPrice.value = null
    customMaxPrice.value = null
  } else if (mode === 'watchlist') {
    loadWatchlist()
  }
}

function selectBracket(b) {
  selectedBracket.value = b
  customMinPrice.value = null
  customMaxPrice.value = null
}

function onCustomPriceChange() {
  selectedBracket.value = 'custom'
}

function resetCustomRange() {
  customMinPrice.value = null
  customMaxPrice.value = null
  selectedBracket.value = 'all'
}

function getHandBadgeClass(price) {
  if (price <= 10) return 'ultra-low'
  if (price <= 20) return 'low'
  if (price <= 50) return 'mid'
  if (price <= 100) return 'high'
  return 'top'
}

function getHandBadgeLabel(price) {
  if (price <= 10) return '极其亲民'
  if (price <= 20) return '低门槛'
  if (price <= 50) return '中等门槛'
  if (price <= 100) return '中高门槛'
  return '大额资金'
}

function openAttribution(stock) {
  selectedStock.value = stock
  attributionVisible.value = true
}

function openKline(stock) {
  selectedStock.value = stock
  klineVisible.value = true
}

function handleGoPaper(stock) {
  attributionVisible.value = false
  emit('switch-tab', { tab: 'paper', stock: stock })
}

async function loadSignals() {
  loading.value = true
  try {
    const params = {
      sort_by: sortBy.value
    }
    const [resSignals, resStats] = await Promise.all([
      api.getSignals(params),
      api.getSignalsStats().catch(() => ({ total_count: 0, brackets: {} }))
    ])
    signals.value = resSignals || []
    if (resStats) stats.value = resStats
  } catch (err) {
    console.error('Failed to load signals:', err)
  } finally {
    loading.value = false
  }
}

// 动态筛选计算
const displaySignals = computed(() => {
  let list = []

  if (viewMode.value === 'watchlist') {
    list = [...watchlist.value]
  } else {
    list = [...(signals.value || [])]

    // 1. 价格区间分类筛选
    if (viewMode.value === 'bracket') {
      if (selectedBracket.value !== 'all' && selectedBracket.value !== 'custom') {
        list = list.filter(s => s.price_bracket === selectedBracket.value)
      }
      // 2. 自定义区间
      if (customMinPrice.value !== null && customMinPrice.value !== '') {
        list = list.filter(s => s.price >= customMinPrice.value)
      }
      if (customMaxPrice.value !== null && customMaxPrice.value !== '') {
        list = list.filter(s => s.price <= customMaxPrice.value)
      }
    }
  }

  // 3. 搜索过滤
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase()
    list = list.filter(
      s => (s.code && s.code.includes(q)) || (s.name && s.name.toLowerCase().includes(q))
    )
  }

  return list
})

const topSignal = computed(() => {
  return displaySignals.value.length > 0 ? displaySignals.value[0] : null
})

const cheapestSignal = computed(() => {
  if (!displaySignals.value.length) return null
  return [...displaySignals.value].sort((a, b) => (a.price || 0) - (b.price || 0))[0]
})

// 分页与全量展示控制 (默认全部36只展示)
const pageSize = ref(36)
const currentPage = ref(1)

const totalPages = computed(() => {
  if (pageSize.value >= displaySignals.value.length) return 1
  return Math.ceil(displaySignals.value.length / pageSize.value) || 1
})

const paginatedSignals = computed(() => {
  if (pageSize.value >= displaySignals.value.length) {
    return displaySignals.value
  }
  const start = (currentPage.value - 1) * pageSize.value
  return displaySignals.value.slice(start, start + pageSize.value)
})

function setPageSize(size) {
  pageSize.value = size
  currentPage.value = 1
}

function changePage(p) {
  if (p < 1 || p > totalPages.value) return
  currentPage.value = p
  if (tableWrapperRef.value) {
    tableWrapperRef.value.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }
}

function getGlobalRank(idx) {
  if (pageSize.value >= displaySignals.value.length) {
    return idx + 1
  }
  return (currentPage.value - 1) * pageSize.value + idx + 1
}

function scrollToTop() {
  const container = document.querySelector('.content-area') || window
  if (container.scrollTo) {
    container.scrollTo({ top: 0, behavior: 'smooth' })
  } else {
    container.scrollTop = 0
  }
}

// 筛选条件变化时自动重置为第1页
watch([viewMode, selectedBracket, customMinPrice, customMaxPrice, searchQuery], () => {
  currentPage.value = 1
})

// 鼠标抓手拖拽滑动 (Drag-to-Scroll)
const tableWrapperRef = ref(null)
const isDragging = ref(false)
let startY = 0
let startScrollTop = 0
let hasMoved = false

function onTableMouseDown(e) {
  if (e.target.closest('button') || e.target.closest('input') || e.target.closest('select') || e.target.closest('a')) {
    return
  }
  if (e.button !== 0) return

  isDragging.value = true
  hasMoved = false
  startY = e.clientY

  const scroller = document.querySelector('.content-area') || window
  startScrollTop = scroller.scrollTop !== undefined ? scroller.scrollTop : window.scrollY

  window.addEventListener('mousemove', onTableMouseMove)
  window.addEventListener('mouseup', onWindowMouseUp)
}

function onTableMouseMove(e) {
  if (!isDragging.value) return
  const deltaY = e.clientY - startY
  if (Math.abs(deltaY) > 4) {
    hasMoved = true
  }
  const scroller = document.querySelector('.content-area') || window
  if (scroller.scrollTop !== undefined) {
    scroller.scrollTop = startScrollTop - deltaY
  } else {
    window.scrollTo(0, startScrollTop - deltaY)
  }
}

function onWindowMouseUp() {
  if (isDragging.value) {
    isDragging.value = false
    setTimeout(() => {
      hasMoved = false
    }, 50)
  }
  window.removeEventListener('mousemove', onTableMouseMove)
  window.removeEventListener('mouseup', onWindowMouseUp)
}

function onRowClick(item) {
  if (hasMoved) return
  openAttribution(item)
}

onUnmounted(() => {
  window.removeEventListener('mousemove', onTableMouseMove)
  window.removeEventListener('mouseup', onWindowMouseUp)
})

onMounted(() => {
  loadSignals()
  loadWatchlist()
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
}

.view-title {
  font-size: 19px;
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
  gap: 10px;
}

.terminal-input {
  background: #161b22;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 13px;
  width: 180px;
  outline: none;
}

.terminal-input:focus {
  border-color: #58a6ff;
}

.action-btn {
  background: #21262d;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  font-weight: 600;
}

.action-btn:hover:not(:disabled) {
  border-color: #58a6ff;
  color: #58a6ff;
}

/* 顶部模式切换 Bar */
.mode-tabs-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #121822;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 6px 12px;
}

.tabs-group {
  display: flex;
  gap: 8px;
}

.mode-tab-btn {
  background: transparent;
  border: 1px solid transparent;
  color: #8b949e;
  padding: 8px 16px;
  border-radius: 6px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  transition: all 0.2s;
  font-size: 14px;
  font-weight: 700;
}

.mode-tab-btn .mode-desc {
  font-size: 11px;
  font-weight: 400;
  color: #6e7681;
}

.mode-tab-btn.active {
  background: #1f6feb;
  color: #ffffff;
}

.mode-tab-btn.active .mode-desc {
  color: #e2e8f0;
}

.mode-tab-btn:hover:not(.active) {
  background: #161b22;
  color: #f0f6fc;
}

.sort-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sort-lbl {
  font-size: 12px;
  color: #8b949e;
}

.terminal-select {
  background: #161b22;
  border: 1px solid #30363d;
  color: #f0f6fc;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 12px;
  outline: none;
}

/* 价格区间选股推荐专属卡片组 */
.bracket-filter-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.bracket-cards-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
}

.bracket-card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 12px 14px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: all 0.2s;
}

.bracket-card:hover {
  border-color: #58a6ff;
  transform: translateY(-2px);
}

.bracket-card.active {
  background: #162234;
  border-color: #388bfd;
  box-shadow: 0 0 0 1px #388bfd;
}

.bc-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.bc-name {
  font-size: 13px;
  font-weight: 700;
  color: #f0f6fc;
}

.bc-count {
  font-size: 11px;
  font-family: monospace;
  background: #21262d;
  color: #8b949e;
  padding: 1px 5px;
  border-radius: 4px;
}

.bc-desc {
  font-size: 11px;
  color: #8b949e;
  line-height: 1.4;
}

.bc-hand {
  font-size: 12px;
  color: #c9d1d9;
  margin-top: 2px;
}

.bc-hand.highlight b {
  color: #00e676;
}

.bracket-card.low.active {
  border-color: #00e676;
}

.bracket-card.mid.active {
  border-color: #388bfd;
}

.bracket-card.high.active {
  border-color: #a855f7;
}

.bracket-card.top.active {
  border-color: #ef4444;
}

/* 自定义价格滑块/区间 */
.custom-range-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  background: #121822;
  border: 1px dashed #30363d;
  border-radius: 6px;
  padding: 8px 14px;
  font-size: 12px;
}

.cr-label {
  color: #8b949e;
}

.range-inputs {
  display: flex;
  align-items: center;
  gap: 8px;
}

.range-input {
  background: #161b22;
  border: 1px solid #30363d;
  color: #f0f6fc;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  width: 90px;
  outline: none;
}

.range-sep {
  color: #8b949e;
}

.reset-range-btn {
  background: #21262d;
  border: 1px solid #30363d;
  color: #8b949e;
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 4px;
  cursor: pointer;
}

.reset-range-btn:hover {
  color: #f0f6fc;
}

.cr-tip {
  color: #58a6ff;
  font-weight: 600;
}

/* 统计卡片 */
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.stat-card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-card.highlight {
  border-color: #388bfd;
  background: #162030;
}

.stat-label {
  font-size: 11px;
  color: #8b949e;
}

.stat-val {
  font-size: 16px;
  font-weight: 700;
  color: #f0f6fc;
}

.stat-val.score {
  font-family: monospace;
  color: #58a6ff;
}

.stat-val.cheapest {
  font-size: 13px;
  color: #00e676;
}

/* 推荐表格控制顶栏 */
.table-header-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  background: #121824;
  border: 1px solid #30363d;
  border-radius: 8px 8px 0 0;
  padding: 10px 16px;
  border-bottom: none;
}

.th-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.th-title {
  font-size: 14px;
  font-weight: 700;
  color: #f0f6fc;
}

.th-count {
  font-size: 12px;
  color: #8b949e;
}

.th-count b {
  color: #58a6ff;
}

.th-drag-hint {
  font-size: 11px;
  color: #eab308;
  background: rgba(234, 179, 8, 0.1);
  border: 1px solid rgba(234, 179, 8, 0.3);
  padding: 2px 8px;
  border-radius: 4px;
}

.th-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.page-size-lbl {
  font-size: 12px;
  color: #8b949e;
}

.page-size-btns {
  display: flex;
  gap: 4px;
}

.ps-btn {
  background: #161b22;
  border: 1px solid #30363d;
  color: #8b949e;
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
}

.ps-btn.active {
  background: #1f6feb;
  color: #ffffff;
  border-color: #388bfd;
  font-weight: 600;
}

.ps-btn:hover:not(.active) {
  color: #f0f6fc;
  border-color: #58a6ff;
}

/* 表格本体与抓手拖拽 */
.table-wrapper {
  background: #161b22;
  border-left: 1px solid #30363d;
  border-right: 1px solid #30363d;
  overflow-x: auto;
  cursor: grab;
  position: relative;
}

.table-wrapper.is-dragging {
  cursor: grabbing;
  user-select: none;
}

.terminal-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  text-align: left;
}

.terminal-table th {
  background: #121822;
  color: #8b949e;
  padding: 11px 12px;
  font-weight: 600;
  border-bottom: 1px solid #30363d;
  position: sticky;
  top: 0;
  z-index: 5;
}

.terminal-table td {
  padding: 10px 12px;
  border-bottom: 1px solid #21262d;
  color: #c9d1d9;
}

/* 推荐表格底栏与翻页 */
.table-footer-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  background: #121824;
  border: 1px solid #30363d;
  border-radius: 0 0 8px 8px;
  padding: 10px 16px;
  border-top: 1px solid #21262d;
}

.tf-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tf-stat {
  font-size: 12px;
  color: #8b949e;
}

.tf-stat b {
  color: #58a6ff;
}

.tf-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.page-nav-btn {
  background: #161b22;
  border: 1px solid #30363d;
  color: #c9d1d9;
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
}

.page-nav-btn:hover:not(:disabled) {
  border-color: #58a6ff;
  color: #58a6ff;
}

.page-nav-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.page-nav-btn.page-num.active {
  background: #1f6feb;
  color: #ffffff;
  border-color: #388bfd;
  font-weight: 700;
}

.all-shown-tip {
  font-size: 12px;
  color: #3fb950;
  font-weight: 500;
}

.scroll-top-btn {
  background: #21262d;
  border: 1px solid #30363d;
  color: #8b949e;
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  margin-left: 8px;
  transition: all 0.15s;
}

.scroll-top-btn:hover {
  color: #58a6ff;
  border-color: #58a6ff;
}

.clickable-row {
  cursor: pointer;
  transition: background 0.15s;
}

.clickable-row:hover {
  background: #1c2128;
}

.rank-badge {
  display: inline-block;
  width: 22px;
  height: 22px;
  line-height: 22px;
  text-align: center;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
  background: #21262d;
  color: #8b949e;
}

.rank-1 { background: #d97706; color: #fff; }
.rank-2 { background: #4b5563; color: #fff; }
.rank-3 { background: #b45309; color: #fff; }

.code-col {
  font-family: monospace;
  color: #58a6ff;
}

.price-col {
  font-family: monospace;
  font-weight: 600;
}

/* 一手资金门槛列 */
.hand-cost-box {
  display: flex;
  align-items: center;
  gap: 8px;
}

.hand-val {
  font-family: monospace;
  font-weight: 600;
  color: #f0f6fc;
}

.hand-badge {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 4px;
  font-weight: 600;
}

.hand-badge.ultra-low {
  background: rgba(0, 230, 118, 0.15);
  color: #00e676;
}

.hand-badge.low {
  background: rgba(56, 139, 253, 0.15);
  color: #58a6ff;
}

.hand-badge.mid {
  background: rgba(168, 85, 247, 0.15);
  color: #a855f7;
}

.hand-badge.high {
  background: rgba(234, 179, 8, 0.15);
  color: #eab308;
}

.hand-badge.top {
  background: rgba(239, 68, 68, 0.15);
  color: #ef4444;
}

/* 价格区间标签 */
.bracket-tag {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
}

.bracket-tag.low {
  background: rgba(0, 230, 118, 0.1);
  color: #00e676;
  border: 1px solid rgba(0, 230, 118, 0.3);
}

.bracket-tag.mid {
  background: rgba(56, 139, 253, 0.1);
  color: #58a6ff;
  border: 1px solid rgba(56, 139, 253, 0.3);
}

.bracket-tag.high {
  background: rgba(168, 85, 247, 0.1);
  color: #a855f7;
  border: 1px solid rgba(168, 85, 247, 0.3);
}

.bracket-tag.top {
  background: rgba(239, 68, 68, 0.1);
  color: #ef4444;
  border: 1px solid rgba(239, 68, 68, 0.3);
}

/* 评分条 */
.score-bar-container {
  position: relative;
  height: 20px;
  background: #21262d;
  border-radius: 4px;
  overflow: hidden;
  display: flex;
  align-items: center;
}

.score-fill {
  position: absolute;
  height: 100%;
  background: linear-gradient(90deg, #1f6feb, #388bfd);
  transition: width 0.3s;
}

.score-text {
  position: relative;
  z-index: 1;
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  padding-left: 8px;
  font-family: monospace;
}

.score-secondary {
  font-family: monospace;
  color: #8b949e;
}

/* 操作组 */
.actions-group {
  display: flex;
  gap: 6px;
}

.action-mini {
  border: none;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.action-mini.star-btn {
  background: #21262d;
  color: #8b949e;
  border: 1px solid #30363d;
}

.action-mini.star-btn.favorited {
  background: rgba(234, 179, 8, 0.15);
  color: #eab308;
  border-color: rgba(234, 179, 8, 0.5);
}

.action-mini.star-btn:hover {
  border-color: #eab308;
  color: #eab308;
}

.action-mini.why {
  background: #21262d;
  color: #58a6ff;
  border: 1px solid #30363d;
}

.action-mini.why:hover {
  border-color: #58a6ff;
}

.action-mini.kline {
  background: #21262d;
  color: #eab308;
  border: 1px solid #30363d;
}

.action-mini.kline:hover {
  border-color: #eab308;
}

.action-mini.paper {
  background: rgba(35, 134, 54, 0.2);
  color: #3fb950;
  border: 1px solid rgba(35, 134, 54, 0.5);
}

.action-mini.paper:hover {
  background: rgba(35, 134, 54, 0.4);
}

/* 自选股管理条 */
.watchlist-toolbar-section {
  background: #121824;
  border: 1px solid #283347;
  border-radius: 8px;
  padding: 12px 18px;
}

.watchlist-info-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.wl-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.wl-title {
  font-size: 15px;
  font-weight: 700;
  color: #eab308;
}

.wl-count {
  font-size: 13px;
  color: #c9d1d9;
}

.wl-count b {
  color: #58a6ff;
}

.wl-tip {
  font-size: 12px;
  color: #8b949e;
}

.wl-add-box {
  display: flex;
  gap: 8px;
}

.wl-input {
  width: 220px;
}

.wl-btn {
  background: #238636;
  border-color: #2ea043;
  color: #ffffff;
}

.wl-btn:hover {
  background: #2ea043;
}

.empty-cell {
  text-align: center;
  padding: 30px;
  color: #8b949e;
}
</style>
