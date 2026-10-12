import * as echarts from 'echarts'

export function buildEquityOption(curve = []) {
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

  return option
}

export function buildComparisonOption(data = {}) {
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

  return option
}
