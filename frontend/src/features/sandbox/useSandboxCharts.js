import { ref } from 'vue'
import * as echarts from 'echarts'
import { buildEquityOption, buildComparisonOption } from './chartOptions.js'

// Each page instance owns its chart instances; the page owns lifecycle cleanup.
export function useSandboxCharts() {
  const curveChartRef = ref(null)
  const compareChartRef = ref(null)
  let curveChartInstance = null
  let compareChartInstance = null

  function updateCurveChart(curve) {
    if (!curveChartRef.value) return
    if (!curveChartInstance) curveChartInstance = echarts.init(curveChartRef.value, 'dark')
    curveChartInstance.setOption(buildEquityOption(curve), true)
  }

  function renderCompareChart(data) {
    if (!compareChartRef.value) return
    if (!compareChartInstance) compareChartInstance = echarts.init(compareChartRef.value, 'dark')
    compareChartInstance.setOption(buildComparisonOption(data), true)
  }

  function handleResize() {
    curveChartInstance?.resize()
    compareChartInstance?.resize()
  }

  function disposeCharts() {
    curveChartInstance?.dispose()
    compareChartInstance?.dispose()
    curveChartInstance = null
    compareChartInstance = null
  }

  return { curveChartRef, compareChartRef, updateCurveChart, renderCompareChart, handleResize, disposeCharts }
}
