<template>
  <div v-if="visible" class="modal-backdrop" @click.self="close">
    <div class="modal-card">
      <!-- 头部股票信息与周期切换 -->
      <div class="modal-header">
        <div class="header-info">
          <span class="stock-title">{{ stock?.name || '--' }}</span>
          <span class="stock-code">{{ stock?.code }}</span>
          <span class="stock-price" :class="priceClass">
            ¥{{ (currentBar?.close || stock?.price || 0).toFixed(2) }}
          </span>
          <span class="stock-diff" :class="priceClass" v-if="currentBar">
            {{ currentBar.diff >= 0 ? '+' : '' }}{{ currentBar.diff.toFixed(2) }}
            ({{ currentBar.diff >= 0 ? '+' : '' }}{{ currentBar.diffPct }}%)
          </span>
        </div>

        <div class="header-controls">
          <div class="period-tabs">
            <button
              :class="{ active: period === 'daily' }"
              @click="switchPeriod('daily')"
            >
              📅 日 K 线
            </button>
            <button
              :class="{ active: period === 'minute' }"
              @click="switchPeriod('minute')"
            >
              ⚡ 1分钟 K 线
            </button>
          </div>
          <button class="close-btn" @click="close" title="关闭窗口">✕</button>
        </div>
      </div>

      <!-- 行情高亮动态指标条（鼠标移动时实时变动） -->
      <div class="kline-ticker-bar" v-if="currentBar">
        <div class="ticker-item"><span class="lbl">日期/时间:</span> <b class="val">{{ currentBar.date }}</b></div>
        <div class="ticker-item"><span class="lbl">开:</span> <b class="val">¥{{ currentBar.open.toFixed(2) }}</b></div>
        <div class="ticker-item"><span class="lbl">高:</span> <b class="val high">¥{{ currentBar.high.toFixed(2) }}</b></div>
        <div class="ticker-item"><span class="lbl">低:</span> <b class="val low">¥{{ currentBar.low.toFixed(2) }}</b></div>
        <div class="ticker-item"><span class="lbl">收:</span> <b class="val" :class="priceClass">¥{{ currentBar.close.toFixed(2) }}</b></div>
        <div class="ticker-item"><span class="lbl">量:</span> <b class="val">{{ (currentBar.volume / 100).toLocaleString() }}手</b></div>
        <div class="ticker-item ma5" v-if="currentBar.ma5"><span class="lbl">MA5:</span> <b class="val">¥{{ currentBar.ma5 }}</b></div>
        <div class="ticker-item ma10" v-if="currentBar.ma10"><span class="lbl">MA10:</span> <b class="val">¥{{ currentBar.ma10 }}</b></div>
        <div class="ticker-item ma20" v-if="currentBar.ma20"><span class="lbl">MA20:</span> <b class="val">¥{{ currentBar.ma20 }}</b></div>
      </div>

      <!-- K线主体画板 -->
      <div class="modal-body">
        <div v-if="loading" class="chart-loading">
          <span class="spinner"></span>
          <span>正在加载并计算高清晰度 K 线行情...</span>
        </div>
        <div v-show="!loading" ref="chartRef" class="chart-canvas"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  stock: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['update:visible'])

const period = ref('daily')
const loading = ref(false)
const chartRef = ref(null)
let chartInstance = null

const currentBar = ref(null)

const priceClass = computed(() => {
  if (!currentBar.value) return 'up'
  return currentBar.value.diff >= 0 ? 'up' : 'down'
})

function close() {
  emit('update:visible', false)
}

function switchPeriod(p) {
  if (period.value === p) return
  period.value = p
  loadData()
}

async function loadData() {
  if (!props.stock?.code) return
  loading.value = true
  try {
    const res = period.value === 'daily'
      ? await api.getDailyKline(props.stock.code)
      : await api.getMinuteKline(props.stock.code)

    renderChart(res)
  } catch (err) {
    console.error('Failed to load kline:', err)
  } finally {
    loading.value = false
  }
}

function renderChart(data) {
  if (!chartRef.value) return
  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value, 'dark')
  }

  const dates = data.dates || []
  const values = data.values || [] // [open, close, lowest, highest]
  const volumes = data.volumes || []
  const mas = data.mas || {}

  // 初始化顶部指标条显示最新一根 K 线
  if (values.length > 0) {
    const lastIdx = values.length - 1
    const [open, close, low, high] = values[lastIdx]
    const diff = close - open
    const diffPct = open > 0 ? (diff / open * 100).toFixed(2) : '0.00'
    currentBar.value = {
      date: dates[lastIdx],
      open,
      high,
      low,
      close,
      diff,
      diffPct,
      volume: volumes[lastIdx] || 0,
      ma5: mas.ma5?.[lastIdx],
      ma10: mas.ma10?.[lastIdx],
      ma20: mas.ma20?.[lastIdx],
    }
  }

  // A股鲜明红涨绿跌
  const upColor = '#ff4d4f'
  const downColor = '#00e676'

  const totalLen = dates.length
  // 默认显示最近 45 根 K 线，确保蜡烛实体饱满清晰可见
  const showCount = period.value === 'daily' ? 45 : 60
  const startPercent = totalLen > showCount ? Math.max(0, 100 - (showCount / totalLen * 100)) : 0

  // 标的参考基准价及目标位/止损位参考线
  const basePrice = props.stock?.price || (values.length > 0 ? values[values.length - 1][1] : 0)
  const targetPrice = basePrice > 0 ? +(basePrice * 1.15).toFixed(2) : null
  const stopLossPrice = basePrice > 0 ? +(basePrice * 0.95).toFixed(2) : null

  const markLines = []
  if (targetPrice) {
    markLines.push({
      yAxis: targetPrice,
      name: '目标止盈 (+15%)',
      lineStyle: { color: '#00e676', type: 'dashed', width: 1.5 },
      label: { formatter: '目标止盈 ¥{c} (+15%)', color: '#00e676', position: 'insideEndTop' }
    })
  }
  if (stopLossPrice) {
    markLines.push({
      yAxis: stopLossPrice,
      name: '硬止损线 (-5%)',
      lineStyle: { color: '#ff4d4f', type: 'dashed', width: 1.5 },
      label: { formatter: '硬止损 ¥{c} (-5%)', color: '#ff4d4f', position: 'insideEndBottom' }
    })
  }

  const option = {
    backgroundColor: '#090d13',
    animation: true,
    legend: {
      data: ['MA5', 'MA10', 'MA20'],
      textStyle: { color: '#8b949e', fontSize: 12 },
      top: '2%',
      right: '3%'
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross',
        lineStyle: { color: '#8b949e', width: 1 }
      },
      backgroundColor: '#161b22',
      borderColor: '#30363d',
      borderWidth: 1,
      textStyle: { color: '#e2e8f0', fontSize: 12 },
      formatter: function (params) {
        if (!params || !params.length) return ''
        const date = params[0].axisValue
        const dataIdx = params[0].dataIndex
        let kItem = params.find(p => p.seriesType === 'candlestick')
        if (kItem && kItem.data) {
          const [open, close, low, high] = kItem.data.slice(1)
          const diff = close - open
          const diffPct = open > 0 ? (diff / open * 100).toFixed(2) : '0.00'
          // 联动同步更新顶部看板
          currentBar.value = {
            date,
            open,
            high,
            low,
            close,
            diff,
            diffPct,
            volume: volumes[dataIdx] || 0,
            ma5: mas.ma5?.[dataIdx],
            ma10: mas.ma10?.[dataIdx],
            ma20: mas.ma20?.[dataIdx],
          }
        }
        return ''
      }
    },
    axisPointer: {
      link: [{ xAxisIndex: 'all' }],
      label: { backgroundColor: '#21262d' }
    },
    grid: [
      { left: '6%', right: '3%', top: '8%', height: '62%' },
      { left: '6%', right: '3%', top: '75%', height: '16%' }
    ],
    xAxis: [
      {
        type: 'category',
        data: dates,
        gridIndex: 0,
        scale: true,
        boundaryGap: true,
        axisLine: { lineStyle: { color: '#30363d' } },
        splitLine: { show: true, lineStyle: { color: 'rgba(48, 54, 61, 0.4)', type: 'dotted' } },
        axisLabel: { color: '#8b949e', fontSize: 11 }
      },
      {
        type: 'category',
        data: dates,
        gridIndex: 1,
        boundaryGap: true,
        axisLine: { lineStyle: { color: '#30363d' } },
        axisLabel: { show: false },
        splitLine: { show: false }
      }
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        splitLine: { lineStyle: { color: 'rgba(48, 54, 61, 0.4)', type: 'dotted' } },
        axisLabel: {
          color: '#8b949e',
          fontSize: 11,
          formatter: (v) => '¥' + v.toFixed(2)
        }
      },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        axisLabel: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false }
      }
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        start: startPercent,
        end: 100
      },
      {
        show: true,
        xAxisIndex: [0, 1],
        type: 'slider',
        top: '94%',
        height: '4%',
        start: startPercent,
        end: 100,
        borderColor: '#30363d',
        fillerColor: 'rgba(56, 139, 253, 0.25)',
        handleStyle: { color: '#58a6ff' },
        textStyle: { color: '#8b949e', fontSize: 10 }
      }
    ],
    series: [
      {
        name: 'KLine',
        type: 'candlestick',
        data: values,
        barMaxWidth: 16,
        barMinWidth: 4,
        itemStyle: {
          color: upColor,
          color0: downColor,
          borderColor: upColor,
          borderColor0: downColor
        },
        markPoint: {
          label: {
            formatter: (param) => param.name + ': ¥' + Number(param.value).toFixed(2),
            color: '#ffffff',
            fontSize: 11,
            backgroundColor: 'rgba(22, 27, 34, 0.85)',
            padding: [3, 6],
            borderRadius: 4
          },
          data: [
            { name: '最高', type: 'max', valueDim: 'highest' },
            { name: '最低', type: 'min', valueDim: 'lowest' }
          ]
        },
        markLine: markLines.length > 0 ? {
          symbol: 'none',
          data: markLines
        } : undefined
      },
      {
        name: 'MA5',
        type: 'line',
        data: mas.ma5 || [],
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#f59e0b', width: 1.8 }
      },
      {
        name: 'MA10',
        type: 'line',
        data: mas.ma10 || [],
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#a855f7', width: 1.8 }
      },
      {
        name: 'MA20',
        type: 'line',
        data: mas.ma20 || [],
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#06b6d4', width: 1.8 }
      },
      {
        name: 'Volume',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        barMaxWidth: 14,
        data: volumes.map((v, i) => {
          const val = values[i]
          const isUp = val ? val[1] >= val[0] : true
          return {
            value: v,
            itemStyle: { color: isUp ? upColor : downColor }
          }
        })
      }
    ]
  }

  chartInstance.setOption(option, true)
}

function handleResize() {
  if (chartInstance) {
    chartInstance.resize()
  }
}

watch(
  () => props.visible,
  (val) => {
    if (val) {
      nextTick(() => {
        loadData()
      })
    } else {
      if (chartInstance) {
        chartInstance.dispose()
        chartInstance = null
      }
    }
  }
)

onMounted(() => {
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  if (chartInstance) chartInstance.dispose()
})
</script>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-card {
  width: 1000px;
  max-width: 96vw;
  height: 620px;
  background: #090d13;
  border: 1px solid #30363d;
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.8), 0 0 0 1px rgba(255, 255, 255, 0.05);
}

.modal-header {
  height: 52px;
  border-bottom: 1px solid #21262d;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  background: #121822;
}

.header-info {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.stock-title {
  font-size: 18px;
  font-weight: 700;
  color: #f0f6fc;
}

.stock-code {
  font-size: 13px;
  font-family: monospace;
  color: #8b949e;
  background: #21262d;
  padding: 2px 6px;
  border-radius: 4px;
}

.stock-price {
  font-size: 18px;
  font-weight: 700;
  font-family: monospace;
}

.stock-price.up, .stock-diff.up {
  color: #ff4d4f;
}

.stock-price.down, .stock-diff.down {
  color: #00e676;
}

.stock-diff {
  font-size: 13px;
  font-weight: 600;
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 16px;
}

.period-tabs {
  display: flex;
  background: #090d13;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 2px;
}

.period-tabs button {
  background: transparent;
  color: #8b949e;
  border: none;
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.period-tabs button.active {
  background: #21262d;
  color: #58a6ff;
  font-weight: 600;
}

.period-tabs button:hover:not(.active) {
  color: #c9d1d9;
}

.close-btn {
  background: transparent;
  color: #8b949e;
  border: none;
  font-size: 18px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
}

.close-btn:hover {
  color: #f0f6fc;
  background: #21262d;
}

/* 顶部动态数据条 */
.kline-ticker-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 20px;
  background: #0d121c;
  border-bottom: 1px solid #21262d;
  font-size: 12px;
  overflow-x: auto;
}

.ticker-item {
  display: flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.ticker-item .lbl {
  color: #8b949e;
}

.ticker-item .val {
  color: #e2e8f0;
  font-family: monospace;
}

.ticker-item .val.high {
  color: #ff4d4f;
}

.ticker-item .val.low {
  color: #00e676;
}

.ticker-item .val.up {
  color: #ff4d4f;
}

.ticker-item .val.down {
  color: #00e676;
}

.ticker-item.ma5 .val {
  color: #f59e0b;
}

.ticker-item.ma10 .val {
  color: #a855f7;
}

.ticker-item.ma20 .val {
  color: #06b6d4;
}

.modal-body {
  flex: 1;
  position: relative;
  background: #090d13;
}

.chart-loading {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #8b949e;
  font-size: 14px;
}

.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #30363d;
  border-top-color: #58a6ff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.chart-canvas {
  width: 100%;
  height: 100%;
}
</style>
