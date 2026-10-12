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

  const signals = ref([])
  const watchlist = ref([])
  const watchlistCodes = computed(() => watchlist.value.map(s => s.code))
  const quickAddInput = ref('')

  const stats = ref({ total_count: 0, brackets: {} })
  const loading = ref(false)
  const statusMessage = ref('')
  let auditTimer = null
  let disposed = false
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
      statusMessage.value = resStats?.readiness?.reason || ''
      if ((resStats?.readiness?.audit_pending || (!signals.value.length && resStats?.total_count > 0)) && !disposed) {
        clearTimeout(auditTimer)
        auditTimer = setTimeout(loadSignals, 2000)
      }
    } catch (err) {
      statusMessage.value = err.message || '推荐读取失败，请重新刷新'
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
    if (loading.value) return
    loading.value = true
    statusMessage.value = '正在根据可信历史重新计算推荐...'
    clearTimeout(auditTimer)
    try {
      let job = await api.refreshSignals()
      while (!disposed) {
        const state = await api.getRecapJob(job.job_id)
        if (state.status === 'failed') throw new Error(state.error || '历史推荐生成失败')
        if (state.status === 'succeeded') {
          if (state.data_only) {
            job = await api.refreshSignals()
            continue
          }
          await loadSignals()
          await loadWatchlist()
          return
        }
        await new Promise(resolve => setTimeout(resolve, 1000))
      }
    } catch (err) {
      statusMessage.value = err.message || '推荐生成失败'
    } finally {
      loading.value = false
    }
  }

  onUnmounted(() => { disposed = true; clearTimeout(auditTimer) })

  return {
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
  }
}
