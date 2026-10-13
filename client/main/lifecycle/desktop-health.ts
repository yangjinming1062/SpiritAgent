import { powerMonitor, screen } from 'electron'

interface DesktopHealthOptions {
  onDisplaysChanged: () => void
  onLock: () => void
  onUnlock: () => void
  onSuspend: () => void
  onResume: () => void
  onHeartbeatLost: () => void
}

// 应用监听在窗口模式继续有效；心跳和延迟重挂载仅属于当前桌面模式。
export function createDesktopHealth(options: DesktopHealthOptions) {
  let listening = false
  let lastHeartbeat = 0
  let healthTimer: ReturnType<typeof setInterval> | undefined
  let displayTimer: ReturnType<typeof setTimeout> | undefined

  const onMetrics = (_event: Electron.Event, _display: Electron.Display, metrics: string[]): void => {
    if (metrics.some(metric => ['bounds', 'scaleFactor', 'rotation'].includes(metric))) {
      options.onDisplaysChanged()
    }
  }

  function start(): void {
    if (listening) {
      return
    }

    listening = true
    screen.on('display-added', options.onDisplaysChanged)
    screen.on('display-removed', options.onDisplaysChanged)
    screen.on('display-metrics-changed', onMetrics)
    powerMonitor.on('lock-screen', options.onLock)
    powerMonitor.on('unlock-screen', options.onUnlock)
    powerMonitor.on('suspend', options.onSuspend)
    powerMonitor.on('resume', options.onResume)
  }

  function heartbeat(): void {
    lastHeartbeat = Date.now()
  }

  function startHeartbeat(): void {
    clearInterval(healthTimer)
    heartbeat()
    healthTimer = setInterval(() => {
      if (Date.now() - lastHeartbeat > 10000) {
        clearInterval(healthTimer)
        healthTimer = undefined
        options.onHeartbeatLost()
      }
    }, 2000)
  }

  function deferDisplayChange(refresh: () => void): void {
    clearTimeout(displayTimer)
    displayTimer = setTimeout(() => {
      displayTimer = undefined
      refresh()
    }, 100)
  }

  function stopMode(): void {
    clearTimeout(displayTimer)
    clearInterval(healthTimer)
    displayTimer = undefined
    healthTimer = undefined
  }

  function dispose(): void {
    stopMode()

    if (!listening) {
      return
    }

    listening = false
    screen.removeListener('display-added', options.onDisplaysChanged)
    screen.removeListener('display-removed', options.onDisplaysChanged)
    screen.removeListener('display-metrics-changed', onMetrics)
    powerMonitor.removeListener('lock-screen', options.onLock)
    powerMonitor.removeListener('unlock-screen', options.onUnlock)
    powerMonitor.removeListener('suspend', options.onSuspend)
    powerMonitor.removeListener('resume', options.onResume)
  }

  return { start, heartbeat, startHeartbeat, deferDisplayChange, stopMode, dispose }
}
