import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { api } from '../../api.js'
import { useTableDrag } from './useTableDrag.js'

// Every invocation creates independent selection/watchlist/filter state.
export function useSignalsPage(emit) {
  const { tableWrapperRef, isDragging, onTableMouseDown, onTableMouseMove, onWindowMouseUp, wasDragged } = useTableDrag()

  const viewMode = ref('bracket') // 'bracket' | 'comprehensive' | 'watchlist'
  const selectedBracket = ref('all') // 'all', 'low', 'mid', 'high', 'top'
  const sortBy = ref('score')
  const customMinPrice = ref(null)
  const customMaxPrice = ref(null)

  const snapshot = ref(null)
  const refreshSubmitting = ref(false)
  const jobRunning = computed(() => refreshSubmitting.value || ['queued', 'running'].includes(snapshot.value?.job?.status))
  const canBuy = computed(() => snapshot.value?.action_permissions?.create_buy === 'allowed')
  const resultLabel = computed(() => ({ current: '当前有效', historical: '历史结果，仅供回看', revoked: '已撤销', none: '尚无结果' })[snapshot.value?.result_state] || '正在读取结果')
  const signals = ref([])
  const watchlist = ref([])
  const watchlistCodes = computed(() => watchlist.value.map(s => s.code))
  const quickAddInput = ref('')

  const stats = ref({ total_count: 0, brackets: {} })
  const loading = ref(false)
  const statusMessage = ref('')
  let auditTimer = null
  let disposed = false
  let requestSequence = 0
  let requestController = null
  let retryDelay = 2000
  let lastQueryKey = null
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
    if (!canBuy.value || stock?.unavailable) return
    attributionVisible.value = false
    emit('switch-tab', { tab: 'paper', stock: stock })
  }

  function formatResultTime(value) {
    if (!value) return '未记录'
    const time = new Date(value)
    return Number.isNaN(time.getTime()) ? value : time.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
  }

  function describeSnapshot(value) {
    const messages = []
    const error = value.job?.error
    if (error) messages.push(typeof error === 'string' ? error : error.message)
    if (value.audit?.error?.message) messages.push(value.audit.error.message)
    if (['queued', 'running'].includes(value.job?.status)) {
      const stages = { queued: '排队', fetch: '采集行情', audit: '核验行情', features: '计算因子', train: '模型计算', score: '候选评分', risk: '风险检查', validate: '校验结果', persist: '保存结果', publish: '发布结果', complete: '完成' }
      messages.push(`更新中：${stages[value.job.stage] || '后台计算'}`)
    } else if (['pending', 'running'].includes(value.audit_state)) messages.push('正在核验行情，已有结果保留')
    if (value.job?.status === 'interrupted') messages.push('上次更新已中断，请重新生成')
    for (const reason of value.blocked_reasons || []) if (reason.message) messages.push(reason.message)
    if (value.empty_kind === 'screened_out') messages.push('本次计算完成，没有满足筛选条件的候选')
    if (value.empty_kind === 'filter_no_match') messages.push('当前筛选条件无匹配候选')
    if (value.empty_kind === 'no_snapshot') messages.push('尚未生成结果，请点击刷新榜单启动初始化')
    return [...new Set(messages.filter(Boolean))].join('；')
  }

  function statsFromSnapshot(value) {
    const brackets = {}
    for (const [key, count] of Object.entries(value.counts.brackets)) {
      const rows = value.rows.filter(row => row.price_bracket === key)
      brackets[key] = { key, count, min_hand_cost: rows.length ? Math.min(...rows.map(row => row.hand_cost || row.price * 100)) : 0,
        top_stock: rows.length ? `${rows[0].name} (¥${rows[0].price})` : '' }
    }
    return { total_count: value.counts.filtered_total, brackets }
  }

  function scheduleRead(delay) {
    clearTimeout(auditTimer)
    if (!disposed) auditTimer = setTimeout(loadSignals, delay)
  }

  async function loadSignals() {
    if (disposed) return
    const sequence = ++requestSequence
    clearTimeout(auditTimer)
    requestController?.abort()
    requestController = new AbortController()
    loading.value = true
    try {
      const params = { sort_by: sortBy.value }
      const queryKey = JSON.stringify(params)
      const response = await api.getSignalsSnapshot(params, { signal: requestController.signal })
      if (disposed || sequence !== requestSequence) return
      if (!response || !Array.isArray(response.rows) || !response.counts || response.counts.filtered_total !== response.rows.length) {
        throw new Error('结果响应不完整，请重新读取')
      }
      if (lastQueryKey === queryKey && snapshot.value && response.response_revision < snapshot.value.response_revision) {
        scheduleRead(2000)
        return
      }
      snapshot.value = response
      signals.value = response.rows
      stats.value = statsFromSnapshot(response)
      lastQueryKey = queryKey
      statusMessage.value = describeSnapshot(response)
      retryDelay = 2000
      const pending = ['queued', 'running'].includes(response.job?.status) || ['pending', 'running'].includes(response.audit_state)
      if (pending || response.snapshot_id) scheduleRead(pending ? 2000 : 30000)
    } catch (err) {
      if (disposed || sequence !== requestSequence) return
      if (snapshot.value && snapshot.value.result_state !== 'revoked') {
        snapshot.value = { ...snapshot.value, result_state: snapshot.value.snapshot_id ? 'historical' : 'none',
          action_permissions: { ...snapshot.value.action_permissions, create_buy: 'paused', add_position: 'paused', export_orders: 'paused' } }
      }
      statusMessage.value = `连接异常，状态未确认：${err.message || '推荐读取失败'}；已有内容仅供回看`
      scheduleRead(retryDelay)
      retryDelay = Math.min(retryDelay * 2, 30000)
    } finally {
      if (sequence === requestSequence) loading.value = false
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
    const priced = displaySignals.value.filter(s => s.price > 0)
    if (!priced.length) return null
    return [...priced].sort((a, b) => a.price - b.price)[0]
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
    const row = paginatedSignals.value[idx]
    if (row?.rank) return row.rank
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

  function onRowClick(item) {
    if (wasDragged() || item.unavailable) return
    openAttribution(item)
  }

  onMounted(() => {
    loadSignals()
    loadWatchlist()
  })

  async function refresh() {
    if (jobRunning.value || disposed) return
    refreshSubmitting.value = true
    statusMessage.value = '正在提交后台更新，已有结果保留'
    try {
      const job = await api.refreshSignals()
      if (disposed) return
      if (snapshot.value) snapshot.value = { ...snapshot.value, job }
      await loadSignals()
    } catch (err) {
      if (disposed) return
      statusMessage.value = err.message || '推荐生成提交失败'
      if (snapshot.value) snapshot.value = { ...snapshot.value,
        action_permissions: { ...snapshot.value.action_permissions, create_buy: 'paused', add_position: 'paused', export_orders: 'paused' } }
      scheduleRead(retryDelay)
    } finally {
      refreshSubmitting.value = false
    }
  }

  watch(sortBy, () => { currentPage.value = 1; loadSignals() })
  onUnmounted(() => { disposed = true; ++requestSequence; requestController?.abort(); clearTimeout(auditTimer) })

  return {
    viewMode, selectedBracket, sortBy, customMinPrice, customMaxPrice,
    snapshot, jobRunning, canBuy, resultLabel, formatResultTime, signals, watchlist, watchlistCodes, quickAddInput, stats,
    loading, statusMessage, searchQuery, klineVisible, attributionVisible, selectedStock,
    displaySignals, topSignal, cheapestSignal, pageSize, currentPage,
    totalPages, paginatedSignals, tableWrapperRef, isDragging, refresh,
    isFavorited, toggleWatchlist, handleQuickAddWatchlist, loadWatchlist, switchViewMode,
    selectBracket, onCustomPriceChange, resetCustomRange, getHandBadgeClass, getHandBadgeLabel,
    openAttribution, openKline, handleGoPaper, loadSignals, setPageSize,
    changePage, getGlobalRank, scrollToTop, onTableMouseDown, onTableMouseMove,
    onWindowMouseUp, onRowClick
  }
}
