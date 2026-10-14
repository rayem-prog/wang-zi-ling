import test from 'node:test'
import assert from 'node:assert/strict'
import axios from 'axios'

const gets = {
  getStatus: '/status', getSignalsStats: '/signals/stats', getOrders: '/orders',
  getMacro: '/macro', getHoldings: '/holdings', getPaper: '/holdings/paper',
  getRecapList: '/recap/list', getRecapSummary: '/recap/status/summary',
  getSettings: '/settings', getIntradayDecisions: '/intraday/decisions',
  getIntradayRadar: '/intraday/radar', getPaperAccount: '/paper/account',
  getWatchlist: '/signals/watchlist', getSandboxSchemes: '/sandbox/schemes',
  getSandboxStatus: '/sandbox/status', getAiSimulation: '/paper/ai-simulation?risk_pref=balanced',
}
const posts = {
  retryMarketCollection: '/status/refresh',
  stopDaemon: '/settings/daemon/stop', startDaemon: '/settings/daemon/start',
  makeRandomPortfolio: '/paper/random-portfolio', shockPortfolio: '/paper/shock-test',
  resetPaperAccount: '/paper/reset', fastForwardSandbox: '/sandbox/fast-forward',
  runDailyRecap: '/recap/run?fast_mode=false', seedSandboxData: '/sandbox/seed?force=false',
}
const payloads = {
  savePosition: '/holdings', createPaperOrder: '/holdings/paper', updateSettings: '/settings',
  testNotify: '/settings/notify/test', placePaperOrder: '/paper/order',
  addToWatchlist: '/signals/watchlist', initSandbox: '/sandbox/init',
  executeAiSimulation: '/paper/ai-simulation/execute', compareSandboxSchemes: '/sandbox/compare',
}
const cases = [
  ...Object.entries(gets).map(([name,url]) => [name,[], 'get',url]),
  ...Object.entries(posts).map(([name,url]) => [name,[], 'post',url]),
  ...Object.entries(payloads).map(([name,url]) => [name,[{code:'000001'}], 'post',url,{code:'000001'}]),
  ['getSignals', [{bracket:'mid'}], 'get', '/signals', undefined, {bracket:'mid'}],
  ['getRecapReport', ['2026-10-09'], 'get', '/recap/2026-10-09'],
  ['getRecapJob', ['a/b'], 'get', '/recap/jobs/a%2Fb'],
  ['getDailyKline', ['000001'], 'get', '/kline/daily/000001'],
  ['getMinuteKline', ['000001'], 'get', '/kline/minute/000001'],
  ['searchStocks', ['a&b'], 'get', '/stock/search?q=a%26b'],
  ['deletePosition', ['000001'], 'delete', '/holdings/000001'],
  ['removeFromWatchlist', ['000001'], 'delete', '/signals/watchlist/000001'],
  ['stepSandbox', [], 'post', '/sandbox/step', {days:1}],
  ['runDailyRecap', [false], 'post', '/recap/run?fast_mode=false'],
  ['seedSandboxData', [true], 'post', '/sandbox/seed?force=true'],
  ['getAiSimulation', ['conservative'], 'get', '/paper/ai-simulation?risk_pref=conservative'],
  ['executeAiSimulation', [], 'post', '/paper/ai-simulation/execute', {}],
  ['compareSandboxSchemes', [], 'post', '/sandbox/compare', {}],
]

test('all API groups retain URLs, verbs, payloads, defaults and backend results', async () => {
  const previous = axios.defaults.adapter
  let last
  axios.defaults.adapter = async config => {
    last = config
    return {data:{marker:'backend-result'}, status:200, statusText:'OK', headers:{}, config}
  }
  try {
    const {api} = await import('../src/api.js')
    assert.equal(api.getOrdersExportUrl(), '/api/orders/export')
    for (const [name,args,method,url,data,params] of cases) {
      assert.deepEqual(await api[name](...args), {marker:'backend-result'}, name)
      assert.equal(last.method,method,name)
      assert.equal(last.url,url,name)
      assert.deepEqual(last.data ? JSON.parse(last.data) : undefined,data,name)
      assert.deepEqual(last.params,params,name)
      assert.equal(last.baseURL,'/api',name)
      assert.equal(last.timeout,15000,name)
    }
  } finally { axios.defaults.adapter = previous }
})
