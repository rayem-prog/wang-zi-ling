<template>
  <div class="view-container">
    <!-- 顶部主标题与双模式切换 -->
    <div class="view-toolbar">
      <div class="toolbar-left">
        <h2 class="view-title">⏳ AI 历史推演与时间加速沙盒</h2>
        <span class="view-subtitle">可信历史数据 · 逐日决策与成交模拟 · 最新行情及持仓方案</span>
      </div>

      <div class="toolbar-right">
        <!-- 往期数据存入与检查状态 -->
        <button
          class="action-btn seed-btn"
          @click="handleSeedData"
          :disabled="seeding || sandboxBusy || personalBusy || compareLoading || isPlaying"
          title="核验已有真实日线及基准，不生成行情"
        >
          {{ seeding ? '正在核验历史数据...' : '📥 检查往期历史数据' }}
        </button>

        <!-- 多方案同台竞技对比 -->
        <button
          class="action-btn compare-btn"
          @click="openCompareModal"
          :disabled="seeding || sandboxBusy || personalBusy || compareLoading || isPlaying"
          title="同时推演 4 种 AI 方案，同台对比净值与超额 Alpha"
        >
          🏁 AI 多方案竞技对比
        </button>
      </div>
    </div>

    <!-- 顶栏历史数据准备提示条 -->
    <div class="seed-status-bar" v-if="seedInfo">
      <span class="seed-icon">📊</span>
      <span class="seed-text">
        历史数据检查：覆盖 <b>{{ seedInfo.stock_count ?? 0 }}</b> 只股票与沪深300基准，
        区间 <b>{{ seedInfo.min_date || '未知' }}</b> 至 <b>{{ seedInfo.max_date || '未知' }}</b>，
        共 <b>{{ (seedInfo.total_bars ?? 0).toLocaleString() }}</b> 根历史日 K 记录。
      </span>
    </div>

    <div class="custom-config-panel replay-range">
      <label>历史推演起止日期：<input type="date" v-model="dateRange.start_date" :disabled="sandboxBusy || isPlaying" /> — <input type="date" v-model="dateRange.end_date" :disabled="sandboxBusy || isPlaying" /></label>
      <button class="action-btn" @click="handleInitSandbox" :disabled="sandboxBusy || personalBusy || compareLoading || seeding || isPlaying">应用区间并重新推演</button>
      <span>历史时点收盘决策，次日撮合；不使用未来行情生成当日评分。</span>
    </div>
    <section class="chart-card decision-evidence">
      <div class="chart-card-header">
        <b>结合我的持仓的最新交易方案</b>
        <button class="action-btn" @click="loadPersonalPlan" :disabled="personalBusy || sandboxBusy || compareLoading || seeding || isPlaying">{{ personalBusy ? '正在核验与生成…' : '生成 / 更新持仓方案' }}</button>
      </div>
      <p v-if="personalBusy">{{ taskProgress }}</p>
      <p v-if="personalError" role="alert">{{ personalError }}</p>
      <p v-if="!personalPlan && !personalBusy">点击生成，读取当前持仓、成本与配置现金，结合历史因子及最新完整交易日收盘行情。</p>
      <template v-if="personalPlan">
        <p v-if="personalPlan.status === 'blocked'" role="alert">{{ personalPlan.reason }}</p>
        <template v-else>
          <p>行情交易日 {{ personalPlan.trade_date }} · 来源 {{ personalPlan.source }} · 可用现金 ¥{{ personalPlan.cash?.toLocaleString() }} · 当前持仓 {{ personalPlan.holdings?.length || 0 }} 只</p>
          <p>{{ personalPlan.basis }}</p>
          <p v-if="personalPlan.status === 'limited'" role="alert">方案受限：{{ personalPlan.reason }}；可核验 {{ personalPlan.verified_stock_count }} 只，排除 {{ personalPlan.excluded_stock_count }} 只。以下评分用于观察，不作为新开仓建议。</p>
          <p v-if="personalPlan.missing_holdings?.length">以下持仓缺少当日可信价格，无法给出可执行指令：{{ personalPlan.missing_holdings.join('、') }}</p>
          <p>模型状态：{{ personalPlan.decision?.model_audit?.status }} · A/B权重 {{ formatMetric(personalPlan.decision?.w_a) }} / {{ formatMetric(1 - personalPlan.decision?.w_a) }}</p>
          <div class="evidence-scroll">
            <table class="evidence-table"><thead><tr><th>标的</th><th>当日收盘</th><th>综合 / A / B 评分</th><th>目标仓位</th><th>具体操作</th><th>历史依据</th></tr></thead><tbody>
              <tr v-for="row in personalPlan.plan" :key="row.code">
                <td>{{ row.name }} {{ row.code }}</td><td>¥{{ formatMetric(row.price) }}</td>
                <td>{{ formatMetric(row.score_blend) }} / {{ formatMetric(row.score_a) }} / {{ formatMetric(row.score_b) }}</td>
                <td>{{ formatMetric(row.suggested_weight * 100, '%') }}</td>
                <td>{{ actionText(personalPlan.orders, row.code) }}<br />{{ riskLevels(personalPlan, row) }}</td>
                <td>{{ evidenceText(personalPlan.evidence, row.code) }}</td>
              </tr>
            </tbody></table>
          </div>
          <p v-if="!personalPlan.orders?.length">当前约束下没有可执行的新交易指令。</p>
          <p v-for="order in personalPlan.orders" :key="order.code + order.action">{{ order.name }} {{ order.code }}：{{ order.action }} {{ order.shares }} 股，限价 ¥{{ order.limit_low }}–{{ order.limit_high }}；{{ order.reason }}</p>
        </template>
      </template>
    </section>
    <!-- 1. AI 选股方案配置卡片组 -->
    <div class="scheme-section">
      <div class="section-label-row">
        <span class="sec-badge">AI SCHEMES</span>
        <span class="sec-title">选择 AI 推演策略方案或自定义配置：</span>
      </div>

      <div class="schemes-grid">
        <!-- 方案 1: 强势动量 -->
        <div
          class="scheme-card"
          :class="{ active: currentScheme === 'momentum' }"
          @click="selectScheme('momentum')"
        >
          <div class="sc-header">
            <span class="sc-icon">🚀</span>
            <span class="sc-title">AI 强势动量突破</span>
            <span class="sc-tag momentum">高Alpha弹性</span>
          </div>
          <p class="sc-desc">专攻高综合分、多头突破爆发标的，单票上限 25%，止损 5%，止盈 15%，5日快速轮动。</p>
          <div class="sc-footer">
            <span>止损: <b>-5%</b></span>
            <span>止盈: <b>+15%</b></span>
            <span>持仓: <b>≤4只</b></span>
          </div>
        </div>

        <!-- 方案 2: 稳健价值 -->
        <div
          class="scheme-card"
          :class="{ active: currentScheme === 'value' }"
          @click="selectScheme('value')"
        >
          <div class="sc-header">
            <span class="sc-icon">🛡️</span>
            <span class="sc-title">AI 低价稳健方案</span>
            <span class="sc-tag value">防御低回撤</span>
          </div>
          <p class="sc-desc">偏好低价池 (≤¥20)，按历史技术评分筛选，单票上限 15%，止损 7%，止盈 12%，稳健长跑。</p>
          <div class="sc-footer">
            <span>止损: <b>-7%</b></span>
            <span>止盈: <b>+12%</b></span>
            <span>持仓: <b>≤6只</b></span>
          </div>
        </div>

        <!-- 方案 3: 双引擎均衡 -->
        <div
          class="scheme-card"
          :class="{ active: currentScheme === 'balanced' }"
          @click="selectScheme('balanced')"
        >
          <div class="sc-header">
            <span class="sc-icon">⚖️</span>
            <span class="sc-title">AI 双引擎多因子均衡</span>
            <span class="sc-tag balanced">量化中枢</span>
          </div>
          <p class="sc-desc">LightGBM 机器学习与线性模型动态加权均衡配置，单票上限 20%，止损 6%，止盈 15%。</p>
          <div class="sc-footer">
            <span>止损: <b>-6%</b></span>
            <span>止盈: <b>+15%</b></span>
            <span>持仓: <b>≤5只</b></span>
          </div>
        </div>

        <!-- 方案 4: 自定义选股推演 -->
        <div
          class="scheme-card custom"
          :class="{ active: currentScheme === 'custom' }"
          @click="selectScheme('custom')"
        >
          <div class="sc-header">
            <span class="sc-icon">🛠️</span>
            <span class="sc-title">自定义选股推演方案</span>
            <span class="sc-tag custom">自由调参</span>
          </div>
          <p class="sc-desc">自由选择股票池（如仅自选股⭐）、单票仓位、止损止盈比率与调仓节奏，探究自定义收益。</p>
          <div class="sc-footer">
            <span>标的池: <b>{{ customForm.pool_filter === 'watchlist' ? '仅自选股⭐' : '全市场池' }}</b></span>
            <span>参数: <b>可展开配置</b></span>
          </div>
        </div>
      </div>

      <!-- 自定义方案专属展开面板 -->
      <div class="custom-config-panel" v-if="currentScheme === 'custom'">
        <div class="config-row">
          <div class="cfg-item">
            <label>股票池选择：</label>
            <select v-model="customForm.pool_filter" class="terminal-select">
              <option value="all">已核验历史标的池</option>
              <option value="watchlist">⭐ 仅推演我的自选股 ({{ watchlistCodes.length }} 只)</option>
              <option value="low">🟢 仅黄金低价池 (≤¥20)</option>
            </select>
          </div>

          <div class="cfg-item">
            <label>初始本金：</label>
            <select v-model.number="customForm.initial_cash" class="terminal-select">
              <option :value="100000">10 万现金</option>
              <option :value="500000">50 万现金</option>
              <option :value="1000000">100 万现金 (标准)</option>
              <option :value="2000000">200 万现金</option>
            </select>
          </div>

          <div class="cfg-item">
            <label>单票仓位上限：</label>
            <select v-model.number="customForm.max_single_weight" class="terminal-select">
              <option :value="0.10">10% 仓位 (分散)</option>
              <option :value="0.20">20% 仓位 (标准)</option>
              <option :value="0.30">30% 仓位 (进取)</option>
              <option :value="0.40">40% 仓位 (集中)</option>
            </select>
          </div>

          <div class="cfg-item">
            <label>止损 / 止盈线：</label>
            <div class="dual-inputs">
              <select v-model.number="customForm.stop_loss" class="terminal-select mini">
                <option :value="0.03">-3% 止损</option>
                <option :value="0.05">-5% 止损</option>
                <option :value="0.07">-7% 止损</option>
                <option :value="0.10">-10% 止损</option>
              </select>
              <select v-model.number="customForm.take_profit" class="terminal-select mini">
                <option :value="0.08">+8% 止盈</option>
                <option :value="0.12">+12% 止盈</option>
                <option :value="0.15">+15% 止盈</option>
                <option :value="0.20">+20% 止盈</option>
              </select>
            </div>
          </div>

          <div class="cfg-item">
            <label>调仓轮动节奏：</label>
            <select v-model.number="customForm.rebalance_interval" class="terminal-select">
              <option :value="3">每 3 交易日</option>
              <option :value="5">每 5 交易日 (周频)</option>
              <option :value="10">每 10 交易日 (双周)</option>
              <option :value="20">每 20 交易日 (月频)</option>
            </select>
          </div>

          <button class="action-btn apply-cfg-btn" @click="handleInitSandbox">
            ⚡ 应用参数并初始化推演
          </button>
        </div>
      </div>
    </div>

    <!-- 2. 时间加速与播放控制台 -->
    <div class="time-control-card">
      <div class="tc-top">
        <div class="tc-date-info">
          <span class="tc-badge">TIME-TRAVEL CONTROLLER</span>
          <span class="tc-current-date">
            正在推演：<b>{{ sandbox.current_date || '--' }}</b>
          </span>
          <span class="tc-step-num">
            第 <b>{{ sandbox.current_step || 0 }}</b> / {{ sandbox.total_steps || 0 }} 交易日
          </span>
          <span class="tc-finish-badge" v-if="sandbox.is_finished">🏁 已完成全周期推演</span>
        </div>

        <div class="tc-speed-selector">
          <span class="speed-lbl">加速流速：</span>
          <button
            class="speed-btn"
            :class="{ active: playSpeed === 1 }"
            @click="setPlaySpeed(1)"
          >
            1x (1天/秒)
          </button>
          <button
            class="speed-btn"
            :class="{ active: playSpeed === 3 }"
            @click="setPlaySpeed(3)"
          >
            3x (快步)
          </button>
          <button
            class="speed-btn"
            :class="{ active: playSpeed === 10 }"
            @click="setPlaySpeed(10)"
          >
            10x (飞速)
          </button>
        </div>
      </div>

      <!-- 进度条 -->
      <div class="tc-progress-container">
        <div class="tc-progress-fill" :style="{ width: (sandbox.progress_pct || 0) + '%' }"></div>
      </div>

      <!-- 播放与单步控制按钮组 -->
      <div class="tc-buttons-row">
        <button
          class="tc-btn play-btn"
          v-if="!isPlaying"
          @click="startPlay"
          :disabled="sandboxBusy || personalBusy || compareLoading || seeding || sandbox.is_finished || sandbox.status === 'blocked'"
        >
          ▶ 开始时间加速推演
        </button>
        <button
          class="tc-btn pause-btn"
          v-else
          @click="pausePlay"
        >
          ⏸ 暂停推演
        </button>

        <button
          class="tc-btn step-btn"
          @click="stepDay(1)"
          :disabled="sandboxBusy || personalBusy || compareLoading || seeding || isPlaying || sandbox.is_finished || sandbox.status === 'blocked'"
        >
          ⏭ 单日步进 (+1天)
        </button>

        <button
          class="tc-btn step-btn"
          @click="stepDay(10)"
          :disabled="sandboxBusy || personalBusy || compareLoading || seeding || isPlaying || sandbox.is_finished || sandbox.status === 'blocked'"
        >
          ⏩ 快速推进 (+10天)
        </button>

        <button
          class="tc-btn instant-btn"
          @click="handleFastForward"
          :disabled="sandboxBusy || personalBusy || compareLoading || seeding || isPlaying || sandbox.is_finished || sandbox.status === 'blocked'"
          title="后台逐日推演全周期，可查看任务进度"
        >
          ⚡ 全周期后台推演
        </button>

        <button
          class="tc-btn reset-btn"
          @click="handleInitSandbox"
          :disabled="sandboxBusy || personalBusy || compareLoading || seeding || isPlaying"
        >
          🔄 重置回到起点
        </button>
      </div>
    </div>

    <div v-if="sandboxBusy || personalBusy || compareLoading || seeding || sandboxError || sandbox.status === 'blocked'" class="sandbox-notice" role="status">
      {{ sandboxBusy ? (taskProgress || '正在完成当前推进或配置操作…') : '' }}
      {{ sandboxError || (sandbox.status === 'blocked' ? `推演已阻断：${sandbox.reason || sandbox.blocked_reason || sandbox.risk_reason || '可信数据不足'}` : '') }}
    </div>
    <section class="chart-card decision-evidence" v-if="sandbox.last_decision">
      <b>历史时点决策依据 · {{ sandbox.last_decision.date }}</b>
      <p>模型：{{ sandbox.last_decision.model_audit?.status }} · 宏观档位 {{ sandbox.last_decision.macro?.stance }} · 以下是该历史日的模拟持仓方案。</p>
      <div class="evidence-scroll"><table class="evidence-table"><thead><tr><th>标的</th><th>历史收盘</th><th>综合 / A / B</th><th>目标仓位</th><th>当日指令</th><th>历史依据</th></tr></thead><tbody>
        <tr v-for="row in sandbox.last_decision.plan" :key="row.code"><td>{{ row.name }} {{ row.code }}</td><td>¥{{ formatMetric(row.price) }}</td><td>{{ formatMetric(row.score_blend) }} / {{ formatMetric(row.score_a) }} / {{ formatMetric(row.score_b) }}</td><td>{{ formatMetric(row.suggested_weight * 100, '%') }}</td><td>{{ actionText(sandbox.last_decision.orders, row.code) }}</td><td>{{ evidenceText(sandbox.evidence, row.code) }}</td></tr>
      </tbody></table></div>
      <p v-if="!sandbox.last_decision.plan?.length">该日没有足够可信因子可生成选股方案。</p>
      <p>评分仅使用截至当日的数据；未来五交易日评价与实际模拟成交收益分别计算。</p>
    </section>
    <!-- 3. AI 盈利水平核心指标看板 (探究盈利能力) -->
    <div class="metrics-grid">
      <!-- 策略累计收益率 -->
      <div class="metric-card main">
        <span class="m-title">AI 策略累计收益率</span>
        <span class="m-val" :class="sandbox.metrics?.total_return_pct >= 0 ? 'text-up' : 'text-down'">
          {{ formatMetric(sandbox.metrics?.total_return_pct, '%', true) }}
        </span>
        <span class="m-sub">
          总资产: ¥{{ Number(sandbox.total_equity || sandbox.initial_cash || 1000000).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}
        </span>
      </div>

      <!-- 沪深300基准收益率 -->
      <div class="metric-card">
        <span class="m-title">同期沪深300基准</span>
        <span class="m-val" :class="sandbox.metrics?.benchmark_return_pct >= 0 ? 'text-up' : 'text-down'">
          {{ formatMetric(sandbox.metrics?.benchmark_return_pct, '%', true) }}
        </span>
        <span class="m-sub">基准对照指数走势</span>
      </div>

      <!-- 超额 Alpha -->
      <div class="metric-card highlight">
        <span class="m-title">超额 Alpha 跑赢幅度</span>
        <span class="m-val" :class="sandbox.metrics?.alpha_pct >= 0 ? 'text-up' : 'text-down'">
          {{ formatMetric(sandbox.metrics?.alpha_pct, '%', true) }}
        </span>
        <span class="m-sub">策略大幅跑赢大盘超额收益</span>
      </div>

      <!-- 最大回撤 -->
      <div class="metric-card">
        <span class="m-title">历史最大回撤 (MaxDD)</span>
        <span class="m-val text-down">
          {{ formatMetric(sandbox.metrics?.max_drawdown_pct == null ? null : -Math.abs(sandbox.metrics.max_drawdown_pct), '%') }}
        </span>
        <span class="m-sub">峰值至谷底最大跌幅</span>
      </div>

      <!-- 实盘胜率 -->
      <div class="metric-card">
        <span class="m-title">AI 交易胜率</span>
        <span class="m-val text-warning">
          {{ formatMetric(sandbox.metrics?.win_rate_pct, '%') }}
        </span>
        <span class="m-sub">已结算交易 {{ sandbox.metrics?.total_trades || 0 }} 笔</span>
      </div>

      <!-- 盈亏比与夏普 -->
      <div class="metric-card">
        <span class="m-title">盈亏比 / 年化夏普比率</span>
        <span class="m-val">
          {{ formatMetric(sandbox.metrics?.profit_loss_ratio) }} / {{ formatMetric(sandbox.metrics?.sharpe_ratio) }}
          <small v-if="sandbox.metric_reasons?.profit_loss_ratio" class="metric-note">未定义：{{ sandbox.metric_reasons.profit_loss_ratio }}</small>
        </span>
        <span class="m-sub">胜率与风报比兼顾</span>
      </div>
    </div>

    <!-- 4. 动态资产净值曲线 vs 沪深300基准 (ECharts) -->
    <div class="chart-card">
      <div class="chart-card-header">
        <div class="cch-left">
          <span class="cch-title">📈 策略净值走势 vs 沪深300 基准 (实时动态生成)</span>
          <span class="cch-tip">实线为 AI 策略净值，虚线为同期大盘基准，随时间推进动态延伸</span>
        </div>
        <div class="cch-legend">
          <span class="leg-item"><span class="leg-color strategy"></span>AI 策略净值</span>
          <span class="leg-item"><span class="leg-color benchmark"></span>沪深300基准</span>
        </div>
      </div>
      <div ref="curveChartRef" class="equity-chart-box"></div>
    </div>

    <!-- 5. 推演中的动态持仓与调仓流水表 -->
    <div class="details-split-grid">
      <!-- 左侧：当前推演持仓 -->
      <div class="detail-box">
        <div class="detail-header">
          <p v-if="sandbox.risk_reason" class="dh-tip">{{ sandbox.risk_reason }}</p>
          <h3 class="dh-title">💼 推演当前持仓 ({{ sandbox.positions?.length || 0 }} 只)</h3>
          <span class="dh-cash">可用现金: ¥{{ Number(sandbox.cash || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
        </div>
        <div class="detail-table-wrapper">
          <table class="terminal-table">
            <thead>
              <tr>
                <th>代码</th>
                <th>名称</th>
                <th>股数</th>
                <th>买入成本</th>
                <th>当前价</th>
                <th>持仓市值</th>
                <th>浮动盈亏</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!sandbox.positions || sandbox.positions.length === 0">
                <td colspan="7" class="empty-row">当前持仓为空，时间推进后 AI 将自动选股建仓</td>
              </tr>
              <tr v-for="pos in sandbox.positions" :key="pos.code">
                <td><b>{{ pos.code }}</b></td>
                <td>{{ pos.name }}</td>
                <td>{{ pos.shares }} 股</td>
                <td>¥{{ Number(pos.avg_cost).toFixed(2) }}</td>
                <td>¥{{ Number(pos.current_price).toFixed(2) }}</td>
                <td>¥{{ Number(pos.market_value).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</td>
                <td :class="pos.pnl >= 0 ? 'text-up' : 'text-down'">
                  {{ pos.pnl >= 0 ? '+' : '' }}{{ pos.pnl_pct }}%
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 右侧：AI 调仓流水明细 -->
      <div class="detail-box">
        <div class="detail-header">
          <h3 class="dh-title">📜 AI 调仓与风控执行流水 (最近 30 笔)</h3>
          <span class="dh-tip">包含 AI 智能建仓、达标止盈与硬止损离场</span>
        </div>
        <div class="detail-table-wrapper">
          <table class="terminal-table">
            <thead>
              <tr>
                <th>日期</th>
                <th>标的</th>
                <th>动作</th>
                <th>价格</th>
                <th>股数</th>
                <th>盈亏</th>
                <th>触发原因</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!sandbox.trades || sandbox.trades.length === 0">
                <td colspan="7" class="empty-row">暂无调仓流水，启动时间加速推演后将实时更新</td>
              </tr>
              <tr v-for="(tr, idx) in sandbox.trades" :key="idx">
                <td class="time-cell">{{ tr.date }}</td>
                <td><b>{{ tr.name }}</b> ({{ tr.code }})</td>
                <td>
                  <span class="side-badge" :class="tr.side === '买入' ? 'buy' : 'sell'">
                    {{ tr.side }}
                  </span>
                </td>
                <td>¥{{ Number(tr.price).toFixed(2) }}</td>
                <td>{{ tr.shares }}</td>
                <td :class="tr.pnl >= 0 ? 'text-up' : 'text-down'">
                  <span v-if="tr.side === '卖出'">{{ tr.pnl >= 0 ? '+' : '' }}¥{{ tr.pnl }} ({{ tr.pnl_pct }}%)</span>
                  <span v-else class="text-muted">--</span>
                </td>
                <td class="reason-cell">{{ tr.reason }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 6. 多方案同台竞技对比弹窗 -->
    <div class="modal-backdrop" v-if="compareModalVisible" @click.self="compareModalVisible = false">
      <div class="compare-modal-card">
        <div class="cm-header">
          <h3 class="cm-title">🏁 AI 4 大方案全周期竞技对比</h3>
          <button class="close-btn" @click="compareModalVisible = false">✕</button>
        </div>

        <div class="cm-body">
          <div v-if="compareLoading" class="compare-loading">
            <span class="spinner"></span>
            <span>正在同时推演 4 种方案并测算全周期收益对比...</span>
          </div>

          <div v-else class="compare-content">
            <div v-if="compareData.status === 'blocked'" class="sandbox-notice" role="status">
              对比已阻断：{{ compareData.blocked_reason || '可信数据不足' }}，收益指标未定义。
            </div>
            <!-- 榜单对比表格 -->
            <div class="leaderboard-box">
              <table class="terminal-table">
                <thead>
                  <tr>
                    <th>方案名称</th>
                    <th>累计收益率</th>
                    <th>超额 Alpha</th>
                    <th>最大回撤</th>
                    <th>交易胜率</th>
                    <th>夏普比率</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(summary, skey) in compareData.summary" :key="skey">
                    <td><b>{{ summary.name }}</b><small v-if="summary.status === 'blocked'" class="metric-note">{{ summary.blocked_reason }} · 未定义</small></td>
                    <td :class="summary.total_return_pct >= 0 ? 'text-up' : 'text-down'">
                      {{ formatMetric(summary.total_return_pct, '%', true) }}
                    </td>
                    <td :class="summary.alpha_pct >= 0 ? 'text-up' : 'text-down'">
                      {{ formatMetric(summary.alpha_pct, '%', true) }}
                    </td>
                    <td class="text-down">{{ formatMetric(summary.max_drawdown_pct == null ? null : -Math.abs(summary.max_drawdown_pct), '%') }}</td>
                    <td class="text-warning">{{ formatMetric(summary.win_rate_pct, '%') }}</td>
                    <td><b>{{ formatMetric(summary.sharpe_ratio) }}</b></td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- 对比走势图 -->
            <div ref="compareChartRef" class="compare-chart-canvas"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- K 线查看弹窗 -->
    <KLineModal
      v-model:visible="klineVisible"
      :stock="selectedStockForKline"
    />
  </div>
</template>

<script setup>
import KLineModal from '../components/KLineModal.vue'
import { useSandboxPage } from '../features/sandbox/useSandboxPage.js'
const {
  seeding, seedInfo, taskProgress, dateRange, personalPlan, personalBusy, personalError, loadPersonalPlan, riskLevels, evidenceText, actionText, currentScheme, playSpeed, isPlaying,
  sandboxBusy, sandboxError, sandbox, watchlistCodes, customForm,
  curveChartRef, compareModalVisible, compareLoading, compareData, compareChartRef,
  klineVisible, selectedStockForKline, selectScheme, setPlaySpeed, handleSeedData,
  formatMetric, runSandboxOperation, handleInitSandbox, stepDay, schedulePlay,
  startPlay, pausePlay, handleFastForward, refresh, updateCurveChart,
  openCompareModal, renderCompareChart, handleResize
} = useSandboxPage()
defineExpose({ refresh })
</script>

<style scoped src="../features/sandbox/styles/schemes.css"></style>
<style scoped src="../features/sandbox/styles/playback-and-metrics.css"></style>
<style scoped src="../features/sandbox/styles/results-and-comparison.css"></style>

<style scoped>
.replay-range { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; margin-bottom: 20px; }
.replay-range input { background: #101722; color: #dbe5f4; border: 1px solid #33435b; padding: 8px; border-radius: 6px; }
.decision-evidence { margin-bottom: 20px; padding: 20px; color: #bac5d5; }
.decision-evidence p { line-height: 1.7; margin: 12px 0; }
.evidence-scroll { overflow-x: auto; }
.evidence-table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }
.evidence-table th, .evidence-table td { padding: 12px; border-bottom: 1px solid #263247; min-width: 110px; }
.evidence-table td:last-child { min-width: 250px; line-height: 1.7; }
</style>
