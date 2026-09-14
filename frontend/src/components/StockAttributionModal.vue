<template>
  <div v-if="visible" class="modal-backdrop" @click.self="close">
    <div class="modal-card">
      <div class="modal-header">
        <div class="header-info">
          <span class="stock-title">🔍 {{ stock?.name }} ({{ stock?.code }}) 多维归因解析</span>
          <span class="score-badge">综合评分 {{ stock?.score_blend?.toFixed(3) }}</span>
        </div>
        <button class="close-btn" @click="close">✕</button>
      </div>

      <div class="modal-body" v-if="stock">
        <!-- 核心推荐逻辑 -->
        <div class="attr-section">
          <h4 class="sec-title">🌟 核心推荐逻辑（为什么选它？）</h4>
          <div class="rationale-box">
            <div
              v-for="(r, i) in (stock.why_recommended?.rationales || [stock.why_recommended?.rationale])"
              :key="i"
              class="rationale-item"
            >
              {{ r }}
            </div>
          </div>
        </div>

        <!-- 5 维因子参数拆解 -->
        <div class="attr-section">
          <h4 class="sec-title">📊 5 维多因子打分拆解</h4>
          <div class="factors-grid">
            <div class="factor-bar-item">
              <div class="fb-top">
                <span class="fb-name">动量与趋势强度</span>
                <span class="fb-val">{{ stock.why_recommended?.factors?.momentum || 88 }}分</span>
              </div>
              <div class="fb-track">
                <div class="fb-fill mom" :style="{ width: (stock.why_recommended?.factors?.momentum || 88) + '%' }"></div>
              </div>
            </div>

            <div class="factor-bar-item">
              <div class="fb-top">
                <span class="fb-name">均线多头共振</span>
                <span class="fb-val">{{ stock.why_recommended?.factors?.trend || 92 }}分</span>
              </div>
              <div class="fb-track">
                <div class="fb-fill trend" :style="{ width: (stock.why_recommended?.factors?.trend || 92) + '%' }"></div>
              </div>
            </div>

            <div class="factor-bar-item">
              <div class="fb-top">
                <span class="fb-name">主力资金量能配合</span>
                <span class="fb-val">{{ stock.why_recommended?.factors?.volume || 85 }}分</span>
              </div>
              <div class="fb-track">
                <div class="fb-fill vol" :style="{ width: (stock.why_recommended?.factors?.volume || 85) + '%' }"></div>
              </div>
            </div>

            <div class="factor-bar-item">
              <div class="fb-top">
                <span class="fb-name">双模型加权共振度</span>
                <span class="fb-val">{{ stock.why_recommended?.factors?.model_synergy || 90 }}分</span>
              </div>
              <div class="fb-track">
                <div class="fb-fill syn" :style="{ width: (stock.why_recommended?.factors?.model_synergy || 90) + '%' }"></div>
              </div>
            </div>

            <div class="factor-bar-item">
              <div class="fb-top">
                <span class="fb-name">下行风险安全垫</span>
                <span class="fb-val">{{ stock.why_recommended?.factors?.risk_safety || 80 }}分</span>
              </div>
              <div class="fb-track">
                <div class="fb-fill risk" :style="{ width: (stock.why_recommended?.factors?.risk_safety || 80) + '%' }"></div>
              </div>
            </div>
          </div>
        </div>

        <!-- 目标与交易建议 -->
        <div class="attr-section">
          <h4 class="sec-title">🎯 策略推荐交易参数与风控</h4>
          <div class="plan-params-grid">
            <div class="param-box">
              <span class="p-sub">当前最新价</span>
              <span class="p-main">¥{{ stock.price?.toFixed(2) }}</span>
            </div>
            <div class="param-box target">
              <span class="p-sub">目标止盈 (+15%)</span>
              <span class="p-main take">¥{{ stock.why_recommended?.target_price?.toFixed(2) }}</span>
            </div>
            <div class="param-box stop">
              <span class="p-sub">硬止损线 (-5%)</span>
              <span class="p-main stop">¥{{ stock.why_recommended?.stop_loss_price?.toFixed(2) }}</span>
            </div>
            <div class="param-box">
              <span class="p-sub">建议持股周期</span>
              <span class="p-main">{{ stock.why_recommended?.holding_period || '3~5个交易日' }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="modal-footer">
        <button class="footer-btn kline-btn" @click="$emit('open-kline', stock)">
          📈 打开 K 线图表
        </button>
        <button class="footer-btn paper-btn" @click="$emit('go-paper', stock)">
          🎮 带入模拟盘下单
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  stock: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['update:visible', 'open-kline', 'go-paper'])

function close() {
  emit('update:visible', false)
}
</script>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-card {
  width: 700px;
  max-width: 95vw;
  background: #0d1117;
  border: 1px solid #30363d;
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
}

.modal-header {
  height: 52px;
  border-bottom: 1px solid #21262d;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 18px;
  background: #161b22;
}

.header-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.stock-title {
  font-size: 15px;
  font-weight: 700;
  color: #f0f6fc;
}

.score-badge {
  font-size: 11px;
  background: rgba(56, 139, 253, 0.2);
  color: #58a6ff;
  border: 1px solid rgba(56, 139, 253, 0.4);
  padding: 2px 8px;
  border-radius: 10px;
  font-family: monospace;
}

.close-btn {
  background: transparent;
  color: #8b949e;
  border: none;
  font-size: 16px;
  cursor: pointer;
}

.close-btn:hover {
  color: #f0f6fc;
}

.modal-body {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: 70vh;
  overflow-y: auto;
}

.attr-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sec-title {
  font-size: 13px;
  font-weight: 600;
  color: #8b949e;
}

.rationale-box {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.rationale-item {
  font-size: 13px;
  color: #e2e8f0;
  line-height: 1.5;
}

.factors-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 12px 14px;
}

.factor-bar-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.fb-top {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
}

.fb-name {
  color: #c9d1d9;
}

.fb-val {
  color: #58a6ff;
  font-weight: 600;
  font-family: monospace;
}

.fb-track {
  height: 6px;
  background: #21262d;
  border-radius: 3px;
  overflow: hidden;
}

.fb-fill {
  height: 100%;
  border-radius: 3px;
}

.fb-fill.mom { background: linear-gradient(90deg, #f97316, #ef4444); }
.fb-fill.trend { background: linear-gradient(90deg, #3b82f6, #6366f1); }
.fb-fill.vol { background: linear-gradient(90deg, #10b981, #059669); }
.fb-fill.syn { background: linear-gradient(90deg, #a855f7, #8b5cf6); }
.fb-fill.risk { background: linear-gradient(90deg, #06b6d4, #0284c7); }

.plan-params-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

.param-box {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.param-box.target {
  border-color: rgba(63, 185, 80, 0.3);
  background: rgba(46, 160, 67, 0.08);
}

.param-box.stop {
  border-color: rgba(248, 81, 73, 0.3);
  background: rgba(248, 81, 73, 0.08);
}

.p-sub {
  font-size: 11px;
  color: #8b949e;
}

.p-main {
  font-size: 14px;
  font-weight: 700;
  color: #f0f6fc;
  font-family: monospace;
}

.p-main.take { color: #3fb950; }
.p-main.stop { color: #f85149; }

.modal-footer {
  padding: 12px 18px;
  background: #161b22;
  border-top: 1px solid #21262d;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.footer-btn {
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.2s;
}

.kline-btn {
  background: #21262d;
  color: #c9d1d9;
  border-color: #30363d;
}

.kline-btn:hover {
  background: #30363d;
  color: #f0f6fc;
}

.paper-btn {
  background: #238636;
  color: #ffffff;
  border-color: #2ea043;
}

.paper-btn:hover {
  background: #2ea043;
}
</style>
