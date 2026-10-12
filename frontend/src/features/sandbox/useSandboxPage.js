import { ref, reactive, onMounted, onUnmounted, onActivated, onDeactivated, nextTick } from 'vue'
import { api } from '../../api.js'
import { useSandboxCharts } from './useSandboxCharts.js'

// Request serialization, playback and page lifecycle stay together.
export function useSandboxPage() {
  const { curveChartRef, compareChartRef, updateCurveChart, renderCompareChart, handleResize, disposeCharts } = useSandboxCharts()

  const seeding = ref(false)
  const seedInfo = ref(null)
  const taskProgress = ref('')
  const dateRange = reactive({ start_date: '2023-01-03', end_date: '2024-12-31' })
  const personalPlan = ref(null)
  const personalBusy = ref(false)
  const personalError = ref('')

  const currentScheme = ref('momentum') // 'momentum' | 'value' | 'balanced' | 'custom'
  const playSpeed = ref(3) // 1, 3, 10
  const isPlaying = ref(false)
  let playTimer = null
  let sandboxQueue = Promise.resolve()
  let pendingOperations = 0
  let active = true
  let destroyed = false
  const sandboxBusy = ref(false)
  const sandboxError = ref('')

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
      total_return_pct: null,
      benchmark_return_pct: null,
      alpha_pct: null,
      max_drawdown_pct: null,
      win_rate_pct: null,
      profit_loss_ratio: null,
      total_trades: 0,
      sharpe_ratio: null,
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

  const compareModalVisible = ref(false)
  const compareLoading = ref(false)
  const compareData = ref({ summary: {}, curves: {}, benchmark_curve: [] })

  const klineVisible = ref(false)
  const selectedStockForKline = ref(null)

  function evidenceText(items, code) {
    const item = items?.find(row => row.code === code)
    if (!item || item.unavailable) return '缺少可信历史因子'
    return `截至 ${item.history_end}，5日收益 ${formatMetric(item.ret_5_pct, '%', true)}，20日收益 ${formatMetric(item.ret_20_pct, '%', true)}，20日波动 ${formatMetric(item.vol_20_pct, '%')}，RSI ${formatMetric(item.rsi_14)}；来源 ${item.source}${item.stale ? '（缺少当日数据）' : ''}`
  }

  function riskLevels(plan, row) {
    const held = plan?.holdings?.find(item => item.code === row.code)
    const policy = plan?.decision?.parameters?.risk
    if (held?.cost > 0 && policy) {
      return `持仓成本阈值：止损 ¥${formatMetric(held.cost * (1 - policy.stop_loss))} / 止盈 ¥${formatMetric(held.cost * (1 + policy.take_profit))}`
    }
    return `观察阈值：止损 ¥${formatMetric(row.stop_loss)} / 止盈 ¥${formatMetric(row.take_profit)}`
  }

  function actionText(orders, code) {
    const order = orders?.find(row => row.code === code)
    return order ? `${order.action} ${order.shares}股；${order.reason}` : '无交易指令，继续观察'
  }

  function taskOptions() {
    return { cancelled: () => destroyed, onProgress: info => {
      taskProgress.value = `${info?.message || '正在执行'}${info?.total ? ` (${info.completed}/${info.total})` : ''}`
    } }
  }

  function requestTask(operation, params, legacy, extra = {}) {
    return api.runSandboxJob ? api.runSandboxJob(operation, params || {}, { ...taskOptions(), ...extra }) : legacy()
  }

  async function loadPersonalPlan() {
    if (personalBusy.value || sandboxBusy.value || destroyed) return
    personalBusy.value = true
    personalError.value = ''
    try {
      const result = await requestTask('personal-plan', {}, () => Promise.resolve(null))
      if (!destroyed) personalPlan.value = result
    } catch (err) {
      personalError.value = err?.response?.data?.detail || err.message
    } finally {
      personalBusy.value = false
    }
  }

  function selectScheme(s) {
    if (sandboxBusy.value || personalBusy.value) return
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
      const res = await requestTask('seed', {}, () => api.seedSandboxData(true))
      seedInfo.value = res
      await handleInitSandbox()
    } catch (err) {
      sandboxError.value = err?.response?.data?.detail || err.message
    } finally {
      seeding.value = false
    }
  }

  function formatMetric(value, suffix = '', signed = false) {
    if (value == null || !Number.isFinite(Number(value))) return '--'
    const number = Number(value)
    return `${signed && number > 0 ? '+' : ''}${number.toFixed(2)}${suffix}`
  }

  function runSandboxOperation(operation) {
    pendingOperations++
    sandboxBusy.value = true
    const result = sandboxQueue.then(async () => {
      if (destroyed) return
      sandboxError.value = ''
      try {
        const state = await operation()
        if (destroyed || !state) return
        sandbox.value = state
        if (state.data_info) seedInfo.value = state.data_info
        if (active) updateCurveChart(state.equity_curve)
        if (state.is_finished || state.status === 'blocked') pausePlay()
        return state
      } catch (err) {
        sandboxError.value = err?.response?.data?.detail || err.message || '推演失败'
        pausePlay()
      }
    }).finally(() => {
      pendingOperations--
      sandboxBusy.value = pendingOperations > 0
    })
    sandboxQueue = result
    return result
  }

  function handleInitSandbox() {
    pausePlay()
    const params = {
      scheme: currentScheme.value,
      ...dateRange,
      initial_cash: currentScheme.value === 'custom' ? customForm.initial_cash : 1000000,
      custom_config: currentScheme.value === 'custom' ? { ...customForm } : null,
      watchlist_codes: [...watchlistCodes.value],
    }
    return runSandboxOperation(() => requestTask('init', params, () => api.initSandbox(params)))
  }

  function stepDay(days = 1) {
    if (sandboxBusy.value || destroyed || sandbox.value.is_finished || sandbox.value.status === 'blocked') return sandboxQueue
    return runSandboxOperation(() => requestTask('step', {}, () => api.stepSandbox(days), { days }))
  }

  function schedulePlay() {
    clearTimeout(playTimer)
    if (!isPlaying.value || !active || destroyed) return
    const intervalMs = playSpeed.value === 1 ? 1000 : (playSpeed.value === 3 ? 350 : 100)
    playTimer = setTimeout(async () => {
      playTimer = null
      await stepDay(1)
      schedulePlay()
    }, intervalMs)
  }

  function startPlay() {
    if (sandbox.value.is_finished || sandbox.value.status === 'blocked' || destroyed || !active) return
    isPlaying.value = true
    schedulePlay()
  }

  function pausePlay() {
    isPlaying.value = false
    clearTimeout(playTimer)
    playTimer = null
  }

  function handleFastForward() {
    pausePlay()
    return runSandboxOperation(() => requestTask('fast-forward', {}, () => api.fastForwardSandbox()))
  }

  function refresh() {
    return runSandboxOperation(() => requestTask('status', {}, () => api.getSandboxStatus()))
  }
  onActivated(() => { active = true; handleResize() })
  onDeactivated(() => { active = false; pausePlay() })

  async function openCompareModal() {
    compareModalVisible.value = true
    compareLoading.value = true
    try {
      const params = {
        ...dateRange,
        initial_cash: 1000000,
        custom_config: { ...customForm },
        watchlist_codes: watchlistCodes.value,
      }
      const res = await requestTask('compare', params, () => api.compareSandboxSchemes(params))
      compareData.value = res
      await nextTick()
      renderCompareChart(res)
    } catch (err) {
      sandboxError.value = err?.response?.data?.detail || err.message
      compareModalVisible.value = false
    } finally {
      compareLoading.value = false
    }
  }

  function restoreConfiguration(state) {
    if (!state) return
    if (state.scheme) currentScheme.value = state.scheme
    if (state.start_date) dateRange.start_date = state.start_date
    if (state.end_date) dateRange.end_date = state.end_date
    if (state.scheme === 'custom' && state.config) {
      for (const key of Object.keys(customForm)) {
        if (state.config[key] != null) customForm[key] = state.config[key]
      }
      customForm.initial_cash = state.initial_cash
    }
  }

  onMounted(async () => {
    window.addEventListener('resize', handleResize)
    // 获取自选股
    // Watchlist enrichment may perform a full market audit; it must not delay replay restoration.
    Promise.resolve().then(() => api.getWatchlist()).then(wl => {
      if (!destroyed && wl) watchlistCodes.value = wl.map(s => s.code)
    }).catch(err => console.debug('Failed to get watchlist:', err))

    // Reattach to an existing task after a browser reload; never reset a running replay.
    try {
      const job = api.getCurrentSandboxJob ? await api.getCurrentSandboxJob() : null
      if (destroyed) return
      if (job) {
        const result = await runSandboxOperation(async () => {
          const value = await api.waitSandboxJob(job, taskOptions())
          if (job.operation === 'personal-plan') { personalPlan.value = value; return null }
          if (job.operation === 'seed') { seedInfo.value = value; return null }
          if (job.operation === 'compare') { compareData.value = value; return null }
          return value
        })
        restoreConfiguration(result)
        restoreConfiguration(await refresh())
      } else if (api.runSandboxJob) {
        const result = await refresh()
        restoreConfiguration(result)
      } else {
        await handleInitSandbox()
      }
    } catch (err) { sandboxError.value = err?.response?.data?.detail || err.message }

  })

  onUnmounted(() => {
    destroyed = true
    active = false
    pausePlay()
    window.removeEventListener('resize', handleResize)
    disposeCharts()
  })

  return {
    riskLevels, evidenceText, actionText, seeding, seedInfo, taskProgress, dateRange, personalPlan, personalBusy, personalError, loadPersonalPlan, currentScheme, playSpeed, isPlaying,
    sandboxBusy, sandboxError, sandbox, watchlistCodes, customForm,
    curveChartRef, compareModalVisible, compareLoading, compareData, compareChartRef,
    klineVisible, selectedStockForKline, selectScheme, setPlaySpeed, handleSeedData,
    formatMetric, runSandboxOperation, handleInitSandbox, stepDay, schedulePlay,
    startPlay, pausePlay, handleFastForward, refresh, updateCurveChart,
    openCompareModal, renderCompareChart, handleResize
  }
}
