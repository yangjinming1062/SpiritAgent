import { powerMonitor, screen } from 'electron'

import { CLIENT_LIMITS } from '../../shared/constants'

const DISPLAY_METRICS = new Set(['bounds', 'rotation', 'scaleFactor'])

const RELEVANT_DISPLAY_METRICS =
  process.platform === 'darwin' ? new Set([...DISPLAY_METRICS, 'workArea']) : DISPLAY_METRICS

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
    if (metrics.some(metric => RELEVANT_DISPLAY_METRICS.has(metric))) {
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
      if (Date.now() - lastHeartbeat > CLIENT_LIMITS.desktopHeartbeatTimeoutMs) {
        clearInterval(healthTimer)
        healthTimer = undefined
        options.onHeartbeatLost()
      }
    }, CLIENT_LIMITS.desktopHeartbeatCheckMs)
  }

  function deferDisplayChange(refresh: () => void): void {
    clearTimeout(displayTimer)
    displayTimer = setTimeout(() => {
      displayTimer = undefined
      refresh()
    }, CLIENT_LIMITS.desktopDisplayChangeDebounceMs)
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
