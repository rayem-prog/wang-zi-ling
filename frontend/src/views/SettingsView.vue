<template>
  <div class="view-container">
    <div class="view-toolbar">
      <div class="toolbar-left">
        <h2 class="view-title">⚙️ 守护进程监控与配置中心</h2>
        <span class="view-subtitle">管理盘中自动监控守护、双通道通知渠道及风险控制参数</span>
      </div>
      <div class="toolbar-right">
        <button class="action-btn primary" @click="saveAllSettings" :disabled="saving">
          {{ saving ? '保存中...' : '💾 保存全部设置' }}
        </button>
      </div>
    </div>

    <div class="cards-layout">
      <!-- 1. 守护进程总控 -->
      <div class="setting-card">
        <div class="card-header">
          <h3 class="card-title">🛡️ 盘中守护进程监控 (09:25–15:05)</h3>
        </div>
        <div class="card-body">
          <div class="daemon-status-box" :class="daemonStatus.online ? 'online' : 'offline'">
            <div class="d-dot"></div>
            <div class="d-info">
              <span class="d-title">{{ daemonStatus.title }}</span>
              <span class="d-detail">{{ daemonStatus.detail }}</span>
            </div>
          </div>

          <span class="hint-text" role="status">{{ configApplicationStatus }}</span>
          <div class="daemon-actions">
            <button
              class="action-btn start-btn"
              :disabled="daemonBusy || daemonStatus.online"
              @click="startDaemon"
            >
              ▶️ 启动盘中守护
            </button>
            <button
              class="action-btn stop-btn"
              :disabled="daemonBusy || !daemonStatus.online"
              @click="stopDaemon"
            >
              ⏹️ 停止守护
            </button>
          </div>
          <span class="hint-text">守护进程自动在交易时段（09:25–11:30, 13:00–15:05）轮询监控止损止盈（P0）与区间买入（P1）并调度报警。</span>
        </div>
      </div>

      <!-- 2. 通知渠道设置 -->
      <div class="setting-card">
        <div class="card-header">
          <h3 class="card-title">🔔 报警与通知渠道</h3>
        </div>
        <div class="card-body">
          <div class="form-group">
            <label class="form-label">通知渠道：</label>
            <select v-model="form.notify_backend" class="terminal-select">
              <option value="local">本地</option>
              <option value="webhook">Webhook</option>
              <option value="both">本地 + Webhook</option>
              <option value="mock">测试记录</option>
            </select>
          </div>
          <div class="form-group">
            <label class="check-label">
              <input type="checkbox" v-model="form.enable_mac_notify" />
              <span>开启 macOS 本地原生弹窗与声音通知 (零成本、即时提醒)</span>
            </label>
          </div>

          <div class="form-group">
            <label class="form-label">机器人类型：</label>
            <select v-model="form.webhook_type" class="terminal-select">
              <option value="generic">通用 Webhook</option>
              <option value="wecom">企业微信群机器人</option>
              <option value="feishu">飞书自定义机器人</option>
              <option value="dingtalk">钉钉群自定义机器人</option>
            </select>
          </div>

          <div class="form-group">
            <label class="form-label">Webhook 接收地址：</label>
            <input
              v-model="form.webhook_url"
              type="text"
              placeholder="https://qyapi.weixin.qq.com/... 或 https://open.feishu.cn/..."
              class="terminal-input"
            />
          </div>

          <div class="notify-test-action">
            <button class="action-btn" @click="testNotification" :disabled="testingNotify">
              {{ testingNotify ? '正在推送...' : '🔔 发送测试推送' }}
            </button>
            <span v-if="notifyResult" class="notify-result" :class="notifyResult.success ? 'success' : 'fail'">
              {{ notifyResult.message }}
            </span>
          </div>
        </div>
      </div>

      <!-- 3. 风控与资金参数 -->
      <div class="setting-card">
        <div class="card-header">
          <h3 class="card-title">🎯 风控与账户资金</h3>
        </div>
        <div class="card-body">
          <div class="form-row">
            <div class="form-group half">
              <label class="form-label">账户总资金 (元)：</label>
              <input v-model.number="form.cash" type="number" step="1000" class="terminal-input" />
            </div>
            <div class="form-group half">
              <label class="form-label">单票最大持仓上限：</label>
              <input value="20%" disabled class="terminal-input disabled" />
            </div>
          </div>

          <div class="form-row">
            <div class="form-group half">
              <label class="form-label">硬止损阈值 (默认 -5%)：</label>
              <input v-model.number="form.stop_loss" type="number" step="0.01" min="0.01" max="0.2" class="terminal-input" />
            </div>
            <div class="form-group half">
              <label class="form-label">目标止盈阈值 (默认 +15%)：</label>
              <input v-model.number="form.take_profit" type="number" step="0.01" min="0.05" max="0.5" class="terminal-input" />
            </div>
          </div>

          <div class="form-group">
            <label class="check-label">
              <input type="checkbox" v-model="form.manual_override" />
              <span>手动固定双引擎加权（不勾选则采用近30日回测自适应）</span>
            </label>
          </div>

          <div v-if="form.manual_override" class="form-group">
            <label class="form-label">LightGBM (引擎A) 权重：{{ (form.manual_w_a * 100).toFixed(0) }}%</label>
            <input
              v-model.number="form.manual_w_a"
              type="range"
              min="0.2"
              max="0.8"
              step="0.05"
              class="terminal-slider"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, onActivated, onDeactivated } from 'vue'
import { api } from '../api'

const saving = ref(false)
const daemonBusy = ref(false)
const desiredConfigVersion = ref('')
const configApplicationStatus = computed(() => {
  const desired = desiredConfigVersion.value
  const applied = daemonStatus.value.config_version
  if (!desired) return '配置版本待确认'
  if (!daemonStatus.value.online) return `已保存 ${desired}，守护离线，等待应用`
  if (applied === desired) return `守护已应用配置 ${desired}`
  return `已保存 ${desired}，守护当前 ${applied || '待确认'}，等待下一次心跳应用`
})
let statusTimer = null
let statusFlight = null
let active = true
const testingNotify = ref(false)
const notifyResult = ref(null)

const daemonStatus = ref({
  online: false,
  title: '检测中...',
  detail: '',
  pid: 0
})

const form = ref({
  cash: 100000,
  stop_loss: 0.05,
  take_profit: 0.15,
  manual_override: false,
  manual_w_a: 0.6,
  notify_backend: 'local',
  enable_mac_notify: true,
  webhook_url: '',
  webhook_type: 'generic'
})

async function loadSettingsAndStatus() {
  try {
    const [sRes, stRes] = await Promise.all([
      api.getSettings(),
      api.getStatus()
    ])

    if (sRes) {
      form.value = {
        cash: sRes.account?.cash || 100000,
        stop_loss: sRes.risk?.stop_loss || 0.05,
        take_profit: sRes.risk?.take_profit || 0.15,
        manual_override: sRes.engine?.manual_override || false,
        manual_w_a: sRes.engine?.manual_w_a || 0.6,
        notify_backend: sRes.notify?.backend || 'local',
        enable_mac_notify: sRes.notify?.enable_mac_notify ?? true,
        webhook_url: sRes.notify?.webhook_url || '',
        webhook_type: sRes.notify?.webhook_type || 'generic'
      }
    }

    desiredConfigVersion.value = stRes?.config_version || sRes?.config_version || desiredConfigVersion.value
    if (stRes?.daemon) {
      daemonStatus.value = stRes.daemon
    }
  } catch (err) {
    console.error('Failed to load settings:', err)
  }
}

async function changeDaemon(action) {
  if (daemonBusy.value) return
  daemonBusy.value = true
  try {
    await action()
    await refreshStatus()
  } catch (err) {
    alert('守护操作失败: ' + (err?.response?.data?.detail || err.message))
  } finally {
    daemonBusy.value = false
  }
}
const startDaemon = () => changeDaemon(api.startDaemon)
const stopDaemon = () => changeDaemon(api.stopDaemon)

function refreshStatus() {
  if (statusFlight) return statusFlight
  statusFlight = api.getStatus().then(status => {
    if (active && status?.daemon) daemonStatus.value = status.daemon
    desiredConfigVersion.value = status?.config_version || desiredConfigVersion.value
  }).catch(err => console.debug('Failed to refresh daemon status:', err)).finally(() => {
    statusFlight = null
    clearTimeout(statusTimer)
    if (active) statusTimer = setTimeout(refreshStatus, 5000)
  })
  return statusFlight
}

async function testNotification() {
  testingNotify.value = true
  notifyResult.value = null
  try {
    const res = await api.testNotify({
      notify_backend: form.value.notify_backend,
      webhook_url: form.value.webhook_url,
      webhook_type: form.value.webhook_type,
      enable_mac_notify: form.value.enable_mac_notify
    })
    notifyResult.value = {
      success: res.webhook_ok || res.mac_ok,
      message: [(res.webhook_ok ? 'Webhook 推送成功！' : ''), (res.mac_ok ? 'macOS 本地通知已弹出！' : '')].filter(Boolean).join(' ') || res.message || '未发送：渠道已关闭或推送失败'
    }
  } catch (err) {
    notifyResult.value = { success: false, message: '推送异常: ' + err }
  } finally {
    testingNotify.value = false
  }
}

async function saveAllSettings() {
  saving.value = true
  try {
    const result = await api.updateSettings(form.value)
    desiredConfigVersion.value = result.config_version || desiredConfigVersion.value
    await refreshStatus()
    alert('配置已成功持久化保存到 user_settings.json！')
  } catch (err) {
    alert('保存配置失败: ' + err)
  } finally {
    saving.value = false
  }
}

const refresh = loadSettingsAndStatus
function activate() {
  active = true
  loadSettingsAndStatus()
  refreshStatus()
}
function deactivate() {
  active = false
  clearTimeout(statusTimer)
  statusTimer = null
}
onMounted(activate)
onActivated(activate)
onDeactivated(deactivate)
onUnmounted(deactivate)
defineExpose({ refresh })
</script>

<style scoped>
.view-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px 20px;
  gap: 16px;
  overflow-y: auto;
}

.view-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.view-title {
  font-size: 18px;
  font-weight: 700;
  color: #f0f6fc;
}

.view-subtitle {
  font-size: 12px;
  color: #8b949e;
  margin-top: 2px;
  display: block;
}

.cards-layout {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 800px;
}

.setting-card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  overflow: hidden;
}

.card-header {
  padding: 12px 16px;
  background: #0d1117;
  border-bottom: 1px solid #21262d;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: #f0f6fc;
}

.card-body {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.daemon-status-box {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 6px;
  border: 1px solid #30363d;
  background: #0d1117;
}

.daemon-status-box.online {
  border-color: rgba(63, 185, 80, 0.4);
  background: rgba(46, 160, 67, 0.1);
}

.daemon-status-box.offline {
  border-color: rgba(248, 81, 73, 0.4);
  background: rgba(248, 81, 73, 0.1);
}

.d-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: currentColor;
}

.daemon-status-box.online .d-dot { color: #3fb950; }
.daemon-status-box.offline .d-dot { color: #f85149; }

.d-info {
  display: flex;
  flex-direction: column;
}

.d-title {
  font-size: 14px;
  font-weight: 700;
  color: #f0f6fc;
}

.d-detail {
  font-size: 11px;
  color: #8b949e;
}

.daemon-actions {
  display: flex;
  gap: 10px;
}

.action-btn {
  background: #21262d;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.action-btn:hover:not(:disabled) {
  background: #30363d;
  color: #f0f6fc;
}

.action-btn.primary {
  background: #238636;
  border-color: #2ea043;
  color: #ffffff;
}

.action-btn.primary:hover:not(:disabled) {
  background: #2ea043;
}

.action-btn.start-btn {
  background: rgba(46, 160, 67, 0.2);
  color: #3fb950;
  border-color: rgba(63, 185, 80, 0.4);
}

.action-btn.stop-btn {
  background: rgba(248, 81, 73, 0.2);
  color: #f85149;
  border-color: rgba(248, 81, 73, 0.4);
}

.action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.hint-text {
  font-size: 11px;
  color: #8b949e;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-row {
  display: flex;
  gap: 12px;
}

.form-group.half {
  flex: 1;
}

.form-label {
  font-size: 12px;
  color: #8b949e;
}

.check-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #c9d1d9;
  cursor: pointer;
}

.terminal-input {
  background: #0d1117;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
}

.terminal-input.disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.terminal-select {
  background: #0d1117;
  border: 1px solid #30363d;
  color: #c9d1d9;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
}

.terminal-slider {
  width: 100%;
}

.notify-test-action {
  display: flex;
  align-items: center;
  gap: 12px;
}

.notify-result {
  font-size: 12px;
}

.notify-result.success {
  color: #3fb950;
}

.notify-result.fail {
  color: #f85149;
}
</style>
