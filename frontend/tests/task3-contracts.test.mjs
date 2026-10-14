import { loadComposable } from './helpers/module-loader.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as vue from 'vue'
import axios from 'axios'
import { renderToString } from '@vue/server-renderer'

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

test('holdings automatically replaces pending prices with dated closing valuations and stops polling', async () => {
  let calls = 0
  const view = setup('views/MacroView', {
    getMacro: async () => ({}), getPaper: async () => ({}),
    getHoldings: async () => (++calls === 1
      ? { positions: [{ code: '000001' }], diagnostics: [], valuation_pending: true }
      : { positions: [{ code: '000001' }], valuation_pending: false,
          valuation_message: '截至 2026-10-09 收盘估算盈亏',
          diagnostics: [{ code: '000001', name: '持仓', shares: 100, cost: 10,
                         price: 12, pnl_pct: .2, price_basis: 'close', trade_date: '2026-10-09' }] })
  })
  await view.exposed.refresh()
  assert.equal(view.timers.size, 1)
  view.fire()
  await flush()
  const html = await view.html()
  assert.match(html, /2026-10-09/)
  assert.match(html, /收盘估值/)
  assert.match(html, /20\.00%/)
  assert.equal(view.timers.size, 0)
  view.state.holdingsData.value.valuation_pending = true
  view.state.pollValuation()
  view.hooks.unmounted.forEach(fn => fn())
  assert.equal(view.timers.size, 0)
})
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve() }

function setup(name, api = {}, session = { jobId: null, submission: null }) {
  const file = new URL(`../src/${name}.vue`, import.meta.url)
  const { descriptor } = parse(fs.readFileSync(file, 'utf8'))
  const compiled = compileScript(descriptor, { id: name })
  const hooks = { mounted: [], unmounted: [], activated: [], deactivated: [] }
  const timers = new Map()
  let nextTimer = 0
  const clock = {
    setInterval: (fn) => { const id = ++nextTimer; timers.set(id, fn); return id },
    setTimeout: (fn) => { const id = ++nextTimer; timers.set(id, () => { timers.delete(id); return fn() }); return id },
    clearInterval: id => timers.delete(id), clearTimeout: id => timers.delete(id)
  }
  const deps = { ...vue, ...clock, recapSession: session, api: { getOrdersExportUrl: () => '/api/orders/export', ...api }, marked: { parse: text => text }, echarts: {},
    window: { addEventListener() {}, removeEventListener() {} }, alert() {},
    createWebSocket: () => ({ close() {} }),
    KLineModal: { render: () => null }, StockSearchInput: { render: () => null }, StockAttributionModal: { render: () => null }, TopHeader: { render: () => null },
    IntradayDecisionView: { render: () => null }, PaperTradingView: { render: () => null }, SignalsView: { render: () => null }, OrdersView: { render: () => null }, MacroView: { render: () => null }, RecapView: { render: () => null }, SettingsView: { render: () => null },
    onMounted: fn => hooks.mounted.push(fn), onUnmounted: fn => hooks.unmounted.push(fn),
    onActivated: fn => hooks.activated.push(fn), onDeactivated: fn => hooks.deactivated.push(fn)
  }
  for (const match of descriptor.scriptSetup.content.matchAll(/^import\s+\{([^}]+)\}\s+from\s+['"](\.\.[^'"]+\.js)['"]/gm)) {
    const names = match[1].split(',').map(name => name.trim())
    if (!names.every(name => name in deps)) Object.assign(deps, loadComposable(new URL(match[2], file), deps))
  }
  const code = compiled.content.replace(/^import .*$/gm, '').replace('export default', 'return')
  const entries = Object.entries(deps).filter(([key]) => /^[A-Za-z_$][\w$]*$/.test(key) && key !== 'default')
  const component = new Function(...entries.map(([key]) => key), code)(...entries.map(([, value]) => value))
  let exposed = {}
  const state = component.setup({ systemStatus: {} }, { expose: value => { exposed = value || {} }, emit() {} })
  const renderCode = compileTemplate({
    source: descriptor.template.content, filename: name, id: name,
    compilerOptions: { bindingMetadata: compiled.bindings }
  }).code.replace(/import \{(.*?)\} from "vue"/s, (_, names) => `const { ${names.replace(/ as /g, ': ')} } = Vue`)
    .replace('export function render', 'function render')
  const render = new Function('Vue', `${renderCode}; return render`)(vue)
  return { state, exposed, timers, hooks, session, fire: () => [...timers.values()].forEach(fn => fn()),
    html: () => renderToString(vue.createSSRApp({ setup: () => state, render })) }
}

test('daemon stop and recap job APIs use the backend contract', async () => {
  const requests = []
  axios.defaults.adapter = async config => {
    requests.push({ method: config.method, url: config.url })
    return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config }
  }
  const { api } = await import(`../src/api.js?offline=${Date.now()}`)
  assert.equal(typeof api.stopDaemon, 'function')
  assert.deepEqual(await api.stopDaemon(), { ok: true })
  assert.equal(typeof api.getRecapJob, 'function')
  await api.getRecapJob('job-123')
  assert.deepEqual(requests, [{ method: 'post', url: '/settings/daemon/stop' }, { method: 'get', url: '/recap/jobs/job-123' }])
})

for (const view of ['IntradayDecision', 'PaperTrading', 'Signals', 'Orders', 'Macro', 'Recap', 'Settings']) {
  test(`${view} provides the refresh contract to the app`, () => {
    assert.equal(typeof setup(`views/${view}View`).exposed.refresh, 'function')
  })
}

test('app refresh invokes the active view refresh contract', async () => {
  const app = setup('App', { getStatus: async () => ({ marker: 'fresh' }) })
  let calls = 0
  app.state.currentViewRef.value = { refresh: async () => { calls++ } }
  await app.state.refreshAll()
  assert.equal(calls, 1)
  assert.equal(app.state.systemStatus.value.marker, 'fresh')
})

test('app shows independent data conditions and deduplicates collection retry', async () => {
  const request=deferred();let calls=0
  const app=setup('App',{retryMarketCollection:()=>{calls++;return request.promise},
    getStatus:async()=>({data_update:{job_id:'same-job',status:'running',stage:'fetch_daily'}})})
  app.state.systemStatus.value={data_conditions:{daily:{state:'ready'},minute:{state:'blocked'},training:{state:'blocked'}},
    data_update:{status:'failed',stage:'fetch_minute',error:'分钟超时'}}
  const html=await app.html()
  assert.match(html,/日线：通过/)
  assert.match(html,/分钟线：未通过/)
  assert.match(html,/历史训练：未通过/)
  assert.match(html,/分钟超时/)
  const a=app.state.retryCollection(),b=app.state.retryCollection()
  assert.equal(calls,1)
  request.resolve({job_id:'same-job',status:'running',stage:'fetch_daily'})
  await Promise.all([a,b])
  assert.equal(app.state.systemStatus.value.data_update.job_id,'same-job')
})

test('intraday requests share one flight and stop scheduling on deactivation', async () => {
  const d = deferred(), r = deferred()
  let decisionsCalls = 0, radarCalls = 0
  const view = setup('views/IntradayDecisionView', {
    getIntradayDecisions: () => { decisionsCalls++; return d.promise },
    getIntradayRadar: () => { radarCalls++; return r.promise }
  })
  view.hooks.mounted.forEach(fn => fn())
  view.state.loadData()
  view.state.loadRadar()
  view.fire()
  assert.equal(decisionsCalls, 1)
  assert.equal(radarCalls, 1)
  view.hooks.deactivated.forEach(fn => fn())
  d.resolve({ timestamp: '2026-10-09 14:00:00', stale: true, holdings_decisions: [], orders_triggers: [], summary: {} })
  r.resolve([])
  await flush()
  assert.equal(view.timers.size, 0)
  view.hooks.activated.forEach(fn => fn())
  await flush()
  assert.ok(view.timers.size > 0)
  view.hooks.unmounted.forEach(fn => fn())
  assert.equal(view.timers.size, 0)
})

test('sandbox permits one step while playback, reset and fast forward compete', async () => {
  const pending = deferred()
  let stepCalls = 0, resetCalls = 0, fastCalls = 0
  const view = setup('views/PaperTradingView', {
    stepSandbox: () => { stepCalls++; return pending.promise },
    initSandbox: async () => { resetCalls++; return { current_step: 0, equity_curve: [] } },
    fastForwardSandbox: async () => { fastCalls++; return { current_step: 100, equity_curve: [] } }
  })
  view.state.startPlay()
  view.fire()
  view.fire()
  await flush()
  assert.equal(stepCalls, 1)
  const reset = view.state.handleInitSandbox()
  const fast = view.state.handleFastForward()
  await flush()
  assert.equal(resetCalls, 0)
  assert.equal(fastCalls, 0)
  pending.resolve({ current_step: 1, equity_curve: [], is_finished: false })
  await Promise.all([reset, fast])
  assert.equal(resetCalls, 1)
  assert.equal(fastCalls, 1)
  assert.equal(view.state.sandbox.value.current_step, 100)
  assert.equal(view.timers.size, 0)
})

test('sandbox deactivation waits for current step without restarting playback', async () => {
  const pending = deferred()
  const view = setup('views/PaperTradingView', { stepSandbox: () => pending.promise })
  view.state.startPlay()
  view.fire()
  view.hooks.deactivated.forEach(fn => fn())
  pending.resolve({ current_step: 1, equity_curve: [], is_finished: false })
  await flush()
  assert.equal(view.state.isPlaying.value, false)
  assert.equal(view.timers.size, 0)
})

test('undefined sandbox metrics stay undefined while a real zero is displayed', () => {
  const view = setup('views/PaperTradingView')
  assert.equal(typeof view.state.formatMetric, 'function')
  assert.equal(view.state.formatMetric(null, '%'), '--')
  assert.equal(view.state.formatMetric(undefined, '%'), '--')
  assert.equal(view.state.formatMetric(0, '%'), '0.00%')
})

test('recap submits once and polls the same job without overlapping requests', async () => {
  const pending = deferred()
  let launches = 0, polls = 0, lists = 0
  const view = setup('views/RecapView', {
    runDailyRecap: async () => { launches++; return { job_id: 'job-1', status: 'queued' } },
    getRecapJob: id => { assert.equal(id, 'job-1'); polls++; return pending.promise },
    getRecapSummary: async () => ({ latest_date: '2026-10-09' }),
    getRecapList: async () => { lists++; return ['2026-10-09'] },
    getRecapReport: async () => ({ content: 'completed report' })
  })
  view.state.handleRunRecap()
  view.state.handleRunRecap()
  await flush()
  view.fire()
  view.fire()
  await flush()
  assert.equal(launches, 1)
  assert.equal(polls, 1)
  assert.equal(view.state.runningRecap.value, true)
  pending.resolve({ job_id: 'job-1', status: 'succeeded', result: { success: true, date: '2026-10-09' } })
  await flush()
  assert.equal(view.state.runningRecap.value, false)
  assert.equal(view.state.reportContent.value, 'completed report')
  assert.equal(lists, 1)
  view.hooks.unmounted.forEach(fn => fn())
  assert.equal(view.timers.size, 0)
})

test('settings reads and saves notification backend with daemon application version', async () => {
  let sent
  const view = setup('views/SettingsView', {
    getSettings: async () => ({ notify: { backend: 'webhook', enable_mac_notify: false }, config_version: 'v2' }),
    getStatus: async () => ({ config_version: 'v2', daemon: { online: true, config_version: 'v1' } }),
    updateSettings: async data => { sent = { ...data }; return { config_version: 'v2' } }
  })
  await view.state.loadSettingsAndStatus()
  assert.equal(view.state.form.value.notify_backend, 'webhook')
  await view.state.saveAllSettings()
  assert.equal(sent.notify_backend, 'webhook')
  assert.match(view.state.configApplicationStatus.value, /v2/)
  assert.match(view.state.configApplicationStatus.value, /v1/)
})


test('rendered blocked sandbox shows its reason and undefined metrics without fabricated profit factor', async () => {
  const view = setup('views/PaperTradingView')
  view.state.sandbox.value = {
    status: 'blocked', blocked_reason: 'no_trusted_trading_days', positions: [], trades: [],
    metrics: { total_return_pct: null, benchmark_return_pct: null, alpha_pct: null,
      max_drawdown_pct: null, win_rate_pct: null, profit_loss_ratio: null, sharpe_ratio: null, total_trades: 0 }
  }
  const html = await view.html()
  assert.match(html, /推演已阻断：no_trusted_trading_days/)
  assert.match(html, /-- \/ --/)
  assert.doesNotMatch(html, /1\.00 \/ 0\.00/)
})

test('rendered intraday mixed quotes shows each row date source and stale flag', async () => {
  const view = setup('views/IntradayDecisionView')
  view.state.decisions.value = { timestamp: '2026-10-09 14:05:00', summary: {}, stale: true,
    holdings_decisions: [
      { code: '000001', name: 'fresh', status: 'SAFE_HOLD', price: 10, price_stale: false, trade_date: '2026-10-09', quote_time: '14:05:00', price_source: 'tencent' },
      { code: '000002', name: 'stale', status: 'STALE', price: 8, price_stale: true, trade_date: '2026-10-08', quote_time: '15:00:00', price_source: 'stored' }
    ], orders_triggers: [] }
  const html = await view.html()
  assert.match(html, /行情已过期，指令暂停/)
  assert.match(html, /报价 · 2026-10-09 14:05:00 · tencent/)
  assert.match(html, /陈旧报价 · 2026-10-08 15:00:00 · stored/)
})

test('recap retains job ID on poll failure, then displays blocked reason and stops', async () => {
  let polls = 0, launches = 0
  const view = setup('views/RecapView', {
    runDailyRecap: async () => { launches++; return { job_id: 'blocked-1', status: 'running' } },
    getRecapJob: async () => {
      polls++
      if (polls === 1) throw new Error('temporary offline')
      return { job_id: 'blocked-1', status: 'failed', error: 'trusted_data_missing', result: { success: false, reason: 'trusted_data_missing' } }
    }
  })
  await view.state.handleRunRecap()
  assert.equal(view.state.runningRecap.value, true)
  assert.equal(view.session.jobId, 'blocked-1')
  view.state.handleRunRecap()
  view.fire()
  await flush()
  assert.equal(launches, 1)
  assert.equal(view.state.runningRecap.value, false)
  assert.equal(view.state.runSuccess.value, false)
  assert.match(view.state.runNotice.value, /trusted_data_missing/)
  assert.equal(view.session.jobId, null)
  assert.equal(view.timers.size, 0)
})

test('unmounted recap keeps the server job for a later view and schedules no more polls', async () => {
  const pending = deferred()
  const session = { jobId: null, submission: null }
  const view = setup('views/RecapView', {
    runDailyRecap: async () => ({ job_id: 'resume-1', status: 'running' }),
    getRecapJob: () => pending.promise
  }, session)
  view.state.handleRunRecap()
  await flush()
  view.hooks.unmounted.forEach(fn => fn())
  pending.resolve({ job_id: 'resume-1', status: 'running' })
  await flush()
  assert.equal(view.timers.size, 0)
  assert.equal(session.jobId, 'resume-1')
  let resumed = 0
  const next = setup('views/RecapView', { getRecapJob: async id => {
    assert.equal(id, 'resume-1'); resumed++; return { status: 'running', job_id: id }
  } }, session)
  await next.state.handleRunRecap()
  assert.equal(resumed, 1)
  next.hooks.unmounted.forEach(fn => fn())
  assert.equal(next.timers.size, 0)
})

test('comparison renders blocked schemes with undefined results and reasons', async () => {
  const view = setup('views/PaperTradingView')
  view.state.compareModalVisible.value = true
  view.state.compareData.value = { status: 'blocked', blocked_reason: 'no_trusted_trading_days',
    summary: { momentum: { name: 'momentum', status: 'blocked', blocked_reason: 'no_trusted_trading_days',
      total_return_pct: null, alpha_pct: null, max_drawdown_pct: null, win_rate_pct: null, sharpe_ratio: null } }, curves: {}, benchmark_curve: [] }
  const html = await view.html()
  assert.match(html, /对比已阻断：no_trusted_trading_days/)
  assert.match(html, /未定义/)
  assert.doesNotMatch(html, /\+%|>-%</)
})

test('app uses backend health cache timestamp and stale status in rendered system banner', async () => {
  const view = setup('App')
  view.state.systemStatus.value = { config_version: 'v2', daemon: { config_version: 'v1' },
    data_health: { ready: false, health_checked_at: '2026-10-11T00:00:00+00:00', health_cache_stale: true, updated_at: '2026-10-09T15:00:00' } }
  const html = await view.html()
  assert.match(html, /健康检查 2026-10-11T00:00:00\+00:00/)
  assert.match(html, /缓存已过期，暂停新买入/)
  assert.match(html, /守护应用配置 v1/)
})

test('intraday uses decision summary timestamp and stale count', async () => {
  const view = setup('views/IntradayDecisionView')
  view.state.decisions.value = { summary: { timestamp: '2026-10-09T14:05:00', stale_count: 2 }, holdings_decisions: [], orders_triggers: [] }
  const html = await view.html()
  assert.match(html, /决策更新时间 2026-10-09T14:05:00/)
  assert.match(html, /2 只报价陈旧/)
})

test('websocket manual close cancels an already scheduled reconnect', async () => {
  const saved = { window: globalThis.window, WebSocket: globalThis.WebSocket, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout }
  const timers = new Map(), sockets = []
  let id = 0
  globalThis.window = { location: { protocol: 'http:', host: 'offline.test' } }
  globalThis.setTimeout = fn => { const key = ++id; timers.set(key, fn); return key }
  globalThis.clearTimeout = key => timers.delete(key)
  globalThis.WebSocket = class {
    constructor() { sockets.push(this) }
    close() { this.onclose?.() }
  }
  try {
    const { createWebSocket } = await import('../src/api.js')
    const connection = createWebSocket()
    sockets[0].onclose()
    assert.equal(timers.size, 1)
    connection.close()
    for (const timer of timers.values()) timer()
    assert.equal(sockets.length, 1)
    assert.equal(timers.size, 0)
  } finally {
    Object.assign(globalThis, saved)
  }
})

test('recap refresh reloads report content even when the selected date stays the same', async () => {
  const view = setup('views/RecapView', {
    getRecapList: async () => ['2026-10-09'], getRecapSummary: async () => ({}),
    getRecapReport: async () => ({ content: 'new report version' })
  })
  view.state.selectedDate.value = '2026-10-09'
  view.state.reportContent.value = 'old report version'
  await view.exposed.refresh()
  assert.equal(view.state.reportContent.value, 'new report version')
})

test('recap launch stays locked until its completed report has been published to the view', async () => {
  const list = deferred()
  let launches = 0
  const view = setup('views/RecapView', {
    runDailyRecap: async () => { launches++; return { job_id: 'done-1', status: 'succeeded', result: { success: true, date: '2026-10-09' } } },
    getRecapSummary: async () => ({}), getRecapList: () => list.promise,
    getRecapReport: async () => ({ content: 'done' })
  })
  const first = view.state.handleRunRecap()
  await flush()
  view.state.handleRunRecap()
  assert.equal(launches, 1)
  list.resolve(['2026-10-09'])
  await first
  assert.equal(view.state.reportContent.value, 'done')
  assert.equal(view.state.runningRecap.value, false)
})

test('polled completed recap stays locked while final report is loading', async () => {
  const list = deferred()
  let launches = 0
  const view = setup('views/RecapView', {
    runDailyRecap: async () => { launches++; return {job_id:'second',status:'queued'} },
    getRecapJob: async () => ({job_id:'first',status:'succeeded',result:{success:true,date:'2026-10-09'}}),
    getRecapSummary: async () => ({}), getRecapList: () => list.promise,
    getRecapReport: async () => ({content:'finished report'})
  }, {jobId:'first',submission:null})
  view.state.runningRecap.value = true
  const poll = view.state.pollJob()
  await flush()
  view.state.handleRunRecap()
  assert.equal(launches,0)
  list.resolve(['2026-10-09'])
  await poll
  assert.equal(view.state.runningRecap.value,false)
  assert.equal(view.state.reportContent.value,'finished report')
})

test('selection filters, search and pagination preserve the visible stock order', async () => {
  const view = setup('views/SignalsView')
  view.state.signals.value = [
    { code: '000001', name: 'Alpha', price: 20, price_bracket: 'low' },
    { code: '000002', name: 'Beta', price: 50, price_bracket: 'mid' },
    { code: '000003', name: 'Gamma', price: 100, price_bracket: 'high' },
  ]
  view.state.selectBracket('mid')
  assert.deepEqual(view.state.displaySignals.value.map(s => s.code), ['000002'])
  view.state.onCustomPriceChange()
  view.state.customMinPrice.value = 20
  view.state.customMaxPrice.value = 50
  assert.deepEqual(view.state.displaySignals.value.map(s => s.code), ['000001','000002'])
  view.state.searchQuery.value = 'BETA'
  assert.deepEqual(view.state.displaySignals.value.map(s => s.code), ['000002'])
  view.state.searchQuery.value = ''
  view.state.resetCustomRange()
  await vue.nextTick()
  view.state.setPageSize(2)
  view.state.changePage(2)
  assert.deepEqual(view.state.paginatedSignals.value.map(s => s.code), ['000003'])
  assert.equal(view.state.getGlobalRank(0), 3)
  view.state.selectBracket('low')
  await vue.nextTick()
  assert.equal(view.state.currentPage.value, 1)
  assert.equal(view.state.cheapestSignal.value.code, '000001')
})

test('separate page instances do not share personal selections or sandbox state', () => {
  const first = setup('views/SignalsView'), second = setup('views/SignalsView')
  first.state.searchQuery.value = 'private'
  first.state.watchlist.value.push({code:'000001'})
  assert.equal(second.state.searchQuery.value, '')
  assert.deepEqual(second.state.watchlist.value, [])
  const a = setup('views/PaperTradingView'), b = setup('views/PaperTradingView')
  a.state.customForm.initial_cash = 12345
  a.state.sandbox.value.positions.push({code:'000001'})
  assert.equal(b.state.customForm.initial_cash, 1000000)
  assert.deepEqual(b.state.sandbox.value.positions, [])
})

test('unavailable watchlist rows remain visible and do not open attribution', async () => {
  const view = setup('views/SignalsView', { getWatchlist: async () => [
    {code:'000999',name:'Personal',price:null,unavailable:true,is_watchlist:true}
  ] })
  view.state.switchViewMode('watchlist')
  await flush()
  assert.equal(view.state.displaySignals.value[0].name,'Personal')
  view.state.onRowClick(view.state.displaySignals.value[0])
  assert.equal(view.state.attributionVisible.value,false)
})

test('signal refresh submits background history work and reads the completed dated snapshot', async () => {
  const calls = []
  const view = setup('views/SignalsView', {
    refreshSignals: async () => { calls.push('generate'); return { job_id: 'history-job', status: 'queued' } },
    getSignalsSnapshot: async () => { calls.push('snapshot'); return {
      snapshot_id: 'S-history', data_as_of: '2026-10-09T15:00:00+08:00', result_state: 'historical', response_revision: 2,
      rows: [{code:'000001',name:'Result',price:10,price_bracket:'low',trade_date:'2026-10-09'}],
      counts: {snapshot_total:1,filtered_total:1,brackets:{low:1,mid:0,high:0,top:0}},
      job: {status:'succeeded'}, action_permissions:{create_buy:'paused'}, blocked_reasons:[]
    } },
    getWatchlist: async () => []
  })
  await view.exposed.refresh()
  assert.deepEqual(calls, ['generate','snapshot'])
  assert.equal(view.state.signals.value[0].trade_date,'2026-10-09')
  view.hooks.unmounted.forEach(fn => fn())
})

test('signals polls one snapshot while initializing, then receives rows and matching counts', async () => {
  let reads = 0
  const view = setup('views/SignalsView', {
    getSignalsSnapshot: async () => (++reads === 1 ? {
      snapshot_id:null,result_state:'none',response_revision:1,rows:[],
      counts:{snapshot_total:0,filtered_total:0,brackets:{low:0,mid:0,high:0,top:0}},
      job:{status:'running',stage:'features'},audit_state:'running',action_permissions:{create_buy:'paused'},blocked_reasons:[]
    } : {
      snapshot_id:'S1',result_state:'current',response_revision:2,rows:[{code:'000001',name:'Result',price:10,price_bracket:'low'}],
      counts:{snapshot_total:1,filtered_total:1,brackets:{low:1,mid:0,high:0,top:0}},
      job:{status:'succeeded'},audit_state:'passed',action_permissions:{create_buy:'allowed'},blocked_reasons:[]
    })
  })
  await view.state.loadSignals()
  assert.equal(view.timers.size, 1)
  view.fire()
  await flush()
  assert.equal(view.state.signals.value.length, 1)
  assert.equal(view.state.stats.value.total_count, 1)
  view.hooks.unmounted.forEach(fn => fn())
  assert.equal(view.timers.size, 0)
})

test('sandbox uses background tasks with the selected historical dates', async () => {
  const calls = []
  const view = setup('views/PaperTradingView', {
    runSandboxJob: async (operation, params, options) => {
      calls.push({ operation, params })
      options.onProgress({ message: '核验历史行情', completed: 12, total: 300 })
      return { scheme: params.scheme, current_step: 0, equity_curve: [], data_info: { stock_count: 292, total_bars: 400000 } }
    }
  })
  view.state.dateRange.start_date = '2025-01-02'
  view.state.dateRange.end_date = '2026-10-09'
  await view.state.handleInitSandbox()
  assert.equal(calls[0].operation, 'init')
  assert.equal(calls[0].params.start_date, '2025-01-02')
  assert.equal(calls[0].params.end_date, '2026-10-09')
  assert.equal(view.state.seedInfo.value.stock_count, 292)
  assert.match(view.state.taskProgress.value, /12\/300/)
})

test('personal plan generation serializes clicks and shows holdings, sources and observed factors', async () => {
  const pending = deferred()
  let requests = 0
  const view = setup('views/PaperTradingView', {
    runSandboxJob: () => { requests++; return pending.promise }
  })
  const first = view.state.loadPersonalPlan()
  await view.state.loadPersonalPlan()
  assert.equal(requests, 1)
  pending.resolve({ status: 'ready', trade_date: '2026-10-09', source: 'trusted-fixture', cash: 100000,
    holdings: [{code:'000001',shares:1000}],
    plan: [{code:'000001',name:'Example',price:10,score_blend:70,score_a:60,score_b:80,suggested_weight:.2}],
    orders: [{code:'000001',name:'Example',action:'清仓',shares:1000,reason:'触及止损',limit_low:9.9,limit_high:10}],
    evidence: [{code:'000001',history_end:'2026-10-09',ret_5_pct:1,ret_20_pct:2,vol_20_pct:3,rsi_14:60,source:'trusted-fixture'}],
    decision: {model_audit:{status:'历史标签训练'},w_a:.5} })
  await first
  assert.equal(view.state.personalBusy.value, false)
  const html = await view.html()
  assert.match(html, /2026-10-09/)
  assert.match(html, /trusted-fixture/)
  assert.match(html, /清仓 1000股/)
  assert.match(html, /20日收益 \+2.00%/)
})

test('sandbox initialization reattaches to a running job instead of resetting it', async () => {
  let resets = 0, waits = 0
  const view = setup('views/PaperTradingView', {
    getWatchlist: async () => [],
    getCurrentSandboxJob: async () => ({job_id:'running',operation:'step'}),
    waitSandboxJob: async () => { waits++; return {scheme:'value',start_date:'2025-01-02',end_date:'2026-10-09',current_step:42,equity_curve:[]} },
    runSandboxJob: async operation => { assert.equal(operation,'status'); return {scheme:'value',start_date:'2025-01-02',end_date:'2026-10-09',current_step:42,equity_curve:[]} },
    initSandbox: async () => { resets++ }
  })
  await Promise.all(view.hooks.mounted.map(fn => fn()))
  assert.equal(waits, 1)
  assert.equal(resets, 0)
  assert.equal(view.state.sandbox.value.current_step, 42)
  assert.equal(view.state.dateRange.start_date, '2025-01-02')
  assert.equal(view.state.dateRange.end_date, '2026-10-09')
})

test('held risk thresholds use actual cost rather than the current quote', () => {
  const view=setup('views/PaperTradingView')
  const plan={holdings:[{code:'601857',cost:10}],decision:{parameters:{risk:{stop_loss:.05,take_profit:.15}}}}
  assert.equal(view.state.riskLevels(plan,{code:'601857',price:11.6,stop_loss:11.02,take_profit:13.34}), '持仓成本阈值：止损 ¥9.50 / 止盈 ¥11.50')
})
