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
        <button class="action-btn" @click="refresh" :disabled="loading">
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
        <span class="stat-val score" v-if="topSignal?.price > 0">
          ¥{{ topSignal.price?.toFixed(2) }} (¥{{ topSignal.hand_cost?.toLocaleString() }}/手)
        </span>
        <span class="stat-val score" v-else>--</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">当前最亲民标的</span>
        <span class="stat-val cheapest" v-if="cheapestSignal?.price > 0">
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
              {{ statusMessage || (loading ? '正在读取推荐标的...' : (props.systemStatus?.recommendation?.reason || '当前筛选条件下暂无符合条件的标的。')) }}
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
                <span class="hand-val">{{ item.unavailable ? '暂无有效行情' : `¥${Number(item.hand_cost || item.price * 100).toLocaleString()}` }}</span>
                <span v-if="!item.unavailable" class="hand-badge" :class="getHandBadgeClass(item.price)">
                  {{ getHandBadgeLabel(item.price) }}
                </span>
              </div>
            </td>
            <!-- 价格区间定位 -->
            <td>
              <span class="bracket-tag" :class="item.price_bracket || 'low'">
                {{ item.unavailable ? '数据不可用' : item.bracket_label }}
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
                <button v-if="!item.unavailable" class="action-mini why" @click.stop="openAttribution(item)" title="查看多因子深度归因">
                  🔍 为什么推荐
                </button>
                <button class="action-mini kline" @click.stop="openKline(item)" title="查看高清蜡烛图">
                  📈 K线
                </button>
                <button v-if="!item.unavailable" class="action-mini paper" @click.stop="handleGoPaper(item)" title="直接带入模拟交易下单">
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
import KLineModal from '../components/KLineModal.vue'
import StockAttributionModal from '../components/StockAttributionModal.vue'
import { useSignalsPage } from '../features/signals/useSignalsPage.js'

const props = defineProps({ systemStatus: { type: Object, default: () => ({}) } })
const emit = defineEmits(['switch-tab'])
const {
  viewMode, selectedBracket, sortBy, customMinPrice, customMaxPrice,
  signals, watchlist, watchlistCodes, quickAddInput, stats,
  loading, statusMessage, searchQuery, klineVisible, attributionVisible, selectedStock,
  displaySignals, topSignal, cheapestSignal, pageSize, currentPage,
  totalPages, paginatedSignals, tableWrapperRef, isDragging, refresh,
  isFavorited, toggleWatchlist, handleQuickAddWatchlist, loadWatchlist, switchViewMode,
  selectBracket, onCustomPriceChange, resetCustomRange, getHandBadgeClass, getHandBadgeLabel,
  openAttribution, openKline, handleGoPaper, loadSignals, setPageSize,
  changePage, getGlobalRank, scrollToTop, onTableMouseDown, onTableMouseMove,
  onWindowMouseUp, onRowClick
} = useSignalsPage(emit)
defineExpose({ refresh })
</script>

<style scoped src="../features/signals/styles/toolbar-and-brackets.css"></style>
<style scoped src="../features/signals/styles/filters-and-summary.css"></style>
<style scoped src="../features/signals/styles/table-and-watchlist.css"></style>
