export function createWebSocket(onMessage, onStatusChange) {
  let ws = null
  let timer = null
  let reconnectTimer = null
  let isClosedManually = false

  function connect() {
    reconnectTimer = null
    if (isClosedManually) return
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
        clearTimeout(reconnectTimer)
        reconnectTimer = setTimeout(connect, 3000)
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
      clearTimeout(reconnectTimer)
      reconnectTimer = null
      if (timer) clearInterval(timer)
      if (ws) ws.close()
    }
  }
}
