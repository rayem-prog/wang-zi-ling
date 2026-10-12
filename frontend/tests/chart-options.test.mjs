import test from 'node:test'
import assert from 'node:assert/strict'

// Incorrect series field selection or mixing benchmark and strategy must fail.
test('equity chart preserves dates, strategy returns and benchmark returns', async () => {
  const { buildEquityOption } = await import('../src/features/sandbox/chartOptions.js')
  const option = buildEquityOption([
    { date: '2024-01-02', return_pct: 2, benchmark_return_pct: -1 },
    { date: '2024-01-03', return_pct: null, benchmark_return_pct: 0 },
  ])
  assert.deepEqual(option.xAxis.data, ['2024-01-02', '2024-01-03'])
  assert.deepEqual(option.series[0].data, [2, null])
  assert.deepEqual(option.series[1].data, [-1, 0])
  assert.equal(option.series[0].name, 'AI 策略收益率')
  assert.deepEqual(buildEquityOption().series[0].data, [])
})

test('comparison chart keeps named schemes separate from the benchmark', async () => {
  const { buildComparisonOption } = await import('../src/features/sandbox/chartOptions.js')
  const option = buildComparisonOption({
    curves: { momentum: [{ return_pct: 3 }], custom: [{ return_pct: -2 }] },
    benchmark_curve: [{ date: '2024-01-02', return_pct: 1 }],
  })
  assert.deepEqual(option.xAxis.data, ['2024-01-02'])
  assert.deepEqual(option.series.map(s => [s.name, s.data]), [
    ['AI 强势动量', [3]], ['自定义方案', [-2]], ['沪深300基准', [1]],
  ])
  assert.deepEqual(buildComparisonOption({}).series[0].data, [])
})

test('charts resize, release both instances and can initialize again', async () => {
  const { loadComposable } = await import('./helpers/module-loader.mjs')
  const vue = await import('vue')
  const realEcharts = await import('echarts')
  const instances = []
  const echarts = {
    graphic: realEcharts.graphic,
    init: () => {
      const instance = { options: [], resized: 0, disposed: false,
        setOption(option) { assert.equal(this.disposed,false); this.options.push(option) },
        resize() { assert.equal(this.disposed,false); this.resized++ },
        dispose() { this.disposed=true },
      }
      instances.push(instance)
      return instance
    },
  }
  const { useSandboxCharts } = loadComposable(new URL('../src/features/sandbox/useSandboxCharts.js',import.meta.url), { ...vue, echarts })
  const charts = useSandboxCharts()
  charts.curveChartRef.value = {}
  charts.compareChartRef.value = {}
  charts.updateCurveChart([{date:'2024-01-02',return_pct:2,benchmark_return_pct:0}])
  charts.renderCompareChart({})
  assert.deepEqual(instances[0].options[0].series[0].data,[2])
  charts.handleResize()
  assert.deepEqual(instances.map(instance=>instance.resized),[1,1])
  charts.disposeCharts()
  assert.ok(instances.every(instance=>instance.disposed))
  charts.handleResize()
  charts.updateCurveChart([])
  assert.equal(instances.length,3)
  assert.equal(instances[2].disposed,false)
  charts.disposeCharts()
})
