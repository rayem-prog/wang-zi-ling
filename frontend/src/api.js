import axios from 'axios'

const client = axios.create({
  baseURL: '/api',
  timeout: 15000
})

export const api = {
  getStatus: () => client.get('/status').then(res => res.data),
  getSignals: (params = {}) => client.get('/signals', { params }).then(res => res.data),
  getSignalsStats: () => client.get('/signals/stats').then(res => res.data),
  getOrders: () => client.get('/orders').then(res => res.data),
  getOrdersExportUrl: () => '/api/orders/export',
  getMacro: () => client.get('/macro').then(res => res.data),
  getHoldings: () => client.get('/holdings').then(res => res.data),
  savePosition: (data) => client.post('/holdings', data).then(res => res.data),
  deletePosition: (code) => client.delete(`/holdings/${code}`).then(res => res.data),
  getPaper: () => client.get('/holdings/paper').then(res => res.data),
  createPaperOrder: (data) => client.post('/holdings/paper', data).then(res => res.data),
  getRecapList: () => client.get('/recap/list').then(res => res.data),
  getRecapReport: (date) => client.get(`/recap/${date}`).then(res => res.data),
  getDailyKline: (code) => client.get(`/kline/daily/${code}`).then(res => res.data),
  getMinuteKline: (code) => client.get(`/kline/minute/${code}`).then(res => res.data),
  getSettings: () => client.get('/settings').then(res => res.data),
  updateSettings: (data) => client.post('/settings', data).then(res => res.data),
  testNotify: (data) => client.post('/settings/notify/test', data).then(res => res.data),
  startDaemon: () => client.post('/settings/daemon/start').then(res => res.data),
  searchStocks: (q) => client.get(`/stock/search?q=${encodeURIComponent(q)}`).then(res => res.data),
  getIntradayDecisions: () => client.get('/intraday/decisions').then(res => res.data),
  getIntradayRadar: () => client.get('/intraday/radar').then(res => res.data),
  getPaperAccount: () => client.get('/paper/account').then(res => res.data),
  placePaperOrder: (data) => client.post('/paper/order', data).then(res => res.data),
  makeRandomPortfolio: () => client.post('/paper/random-portfolio').then(res => res.data),
  shockPortfolio: () => client.post('/paper/shock-test').then(res => res.data),
  resetPaperAccount: () => client.post('/paper/reset').then(res => res.data),
  getAiSimulation: (riskPref = 'balanced') => client.get(`/paper/ai-simulation?risk_pref=${riskPref}`).then(res => res.data),
  executeAiSimulation: (data = {}) => client.post('/paper/ai-simulation/execute', data).then(res => res.data),

  // 自选股管理
  getWatchlist: () => client.get('/signals/watchlist').then(res => res.data),
  addToWatchlist: (data) => client.post('/signals/watchlist', data).then(res => res.data),
  removeFromWatchlist: (code) => client.delete(`/signals/watchlist/${code}`).then(res => res.data),

  // 历史推演沙盒与时间加速
  getSandboxSchemes: () => client.get('/sandbox/schemes').then(res => res.data),
  seedSandboxData: (force = false) => client.post(`/sandbox/seed?force=${force}`).then(res => res.data),
  initSandbox: (data) => client.post('/sandbox/init', data).then(res => res.data),
  stepSandbox: (days = 1) => client.post('/sandbox/step', { days }).then(res => res.data),
  fastForwardSandbox: () => client.post('/sandbox/fast-forward').then(res => res.data),
  getSandboxStatus: () => client.get('/sandbox/status').then(res => res.data),
  compareSandboxSchemes: (data = {}) => client.post('/sandbox/compare', data).then(res => res.data)
}

export function createWebSocket(onMessage, onStatusChange) {
  let ws = null
  let timer = null
  let isClosedManually = false

  function connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const wsUrl = `${protocol}//${host}/ws/stream`

    ws = new WebSocket(wsUrl)

    ws.onopen = () => {
      if (onStatusChange) onStatusChange('connected')
      if (timer) clearInterval(timer)
      // 心跳保活
      timer = setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send('ping')
        }
      }, 15000)
    }

    ws.onmessage = (event) => {
      if (event.data === 'pong') return
      try {
        const payload = JSON.parse(event.data)
        if (onMessage) onMessage(payload)
      } catch (err) {
        console.debug('WS message parse error:', err)
      }
    }

    ws.onclose = () => {
      if (onStatusChange) onStatusChange('disconnected')
      if (timer) clearInterval(timer)
      if (!isClosedManually) {
        setTimeout(connect, 3000)
      }
    }

    ws.onerror = () => {
      ws.close()
    }
  }

  connect()

  return {
    close: () => {
      isClosedManually = true
      if (timer) clearInterval(timer)
      if (ws) ws.close()
    }
  }
}
