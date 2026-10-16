import {
  $effectiveTier,
  $spriteState,
  clearVfx,
  emitVfx,
  pushEffectiveDisturbanceTier,
  setSpriteState,
  startAutonomyProvision,
  stopAutonomyProvision
} from '@/modules/character'
import { refreshDesktopVideos } from '@/modules/desktop-videos'
import { cancelVoiceBar, stopSpeaking } from '@/modules/speech'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { SpiritAgentRpcError } from '@/shared/lib/gateway-protocol'
import { log } from '@/shared/lib/log'
import { reconnectBackoffMs } from '@/shared/lib/reconnect'
import { fetchSlashCommandMeta } from '@/shared/lib/slash-commands'
import { gatewayHost } from '@/shared/platform/desktop'
import { SpiritAgentGateway } from '@/shared/spiritagent'
import { reportPrimaryGatewayState, setPrimaryGateway, tearDownPrimaryGateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { getStrings } from '@/shared/strings'
import { errorMessage } from '@runtime'

import { clearDesktopBootFailure, failDesktopBoot } from './boot-store'
import { handleGatewayEvent } from './gateway-event-router'
import { isDeviceCommandEvent } from './gateway-event-util'
import { createHostHistoryRecovery } from './host-history-recovery'
import { createRunnerToolsSync } from './runner-tools-sync'

// 1008 停止重连；会话过期由主进程的鉴权失败通知确认。
const WS_CLOSE_POLICY_VIOLATION = 1008

// 取消计时器并返回 null，供调用方复位持有它的变量。
function clearTimer(timer: ReturnType<typeof setTimeout> | null): null {
  if (timer !== null) {
    clearTimeout(timer)
  }

  return null
}

// 重连后补报离线期间可能变化的生效档位。
function syncDisturbanceTier(): void {
  const tier = $effectiveTier.get()

  if (!tier) {
    return
  }

  pushEffectiveDisturbanceTier(tier)
}

// 夜间调度与互动统计按本地 IANA 时区聚合。
function syncTimezone(gateway: SpiritAgentGateway): void {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone

  if (!timezone) {
    return
  }

  // 上报失败时服务端沿用旧值或回落 UTC，只记录诊断。
  void gateway
    .request('companion.set_timezone', { timezone })
    .catch(error => log.warn('gateway-boot', 'companion.set_timezone failed', error))
}

// 账户装配创建独立实例；启动和释放在 React 之外保持成对、幂等。
export function createGatewayHostRuntime() {
  let started = false
  let disposed = false
  let stop: (() => void) | undefined

  return {
    start(): void {
      if (started || disposed) {
        return
      }

      started = true
      stop = startGatewayHost()
    },
    dispose(): void {
      if (disposed) {
        return
      }

      disposed = true
      stop?.()
      stop = undefined
    }
  }
}

function startGatewayHost(): (() => void) | undefined {
  let cancelled = false
  const isAuthCurrent = captureAuthScope()
  const desktop = gatewayHost()

  if (!desktop) {
    failDesktopBoot(getStrings().boot.errors.bridgeUnavailable)

    return
  }

  // 初次启动后按退避重连，电源恢复、网络上线和窗口可见时立即重试。
  let bootCompleted = false
  let reconnecting = false
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let graceTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectAttempt = 0
  let lastReconnectError: Error | null = null
  let reconnectErrorNotified = false

  const gatewayOpen = () => gateway.connectionState === 'open'

  const connectOnce = async (): Promise<void> => {
    const wsUrl = await desktop.getGatewayWsUrl()

    if (cancelled) {
      return
    }

    await gateway.connect(wsUrl)

    if (cancelled) {
      return
    }

    void syncTools()
  }

  const attemptReconnect = async () => {
    if (cancelled || reconnecting || gatewayOpen() || gateway.lastCloseCode === WS_CLOSE_POLICY_VIOLATION) {
      return
    }

    reconnecting = true

    try {
      await connectOnce()
    } catch (error) {
      if (cancelled) {
        return
      }

      lastReconnectError = error instanceof Error ? error : new Error(String(error))
      log.warn('gateway-boot', 'attemptReconnect failed', lastReconnectError)
    } finally {
      reconnecting = false

      if (!cancelled && !gatewayOpen()) {
        scheduleReconnect()

        if (reconnectAttempt >= 5 && !reconnectErrorNotified && lastReconnectError) {
          reconnectErrorNotified = true
          notifyError(lastReconnectError, getStrings().boot.errors.desktopReconnectFailed)
        }
      }
    }
  }

  function scheduleReconnect(): void {
    if (
      cancelled ||
      reconnecting ||
      reconnectTimer !== null ||
      gatewayOpen() ||
      gateway.lastCloseCode === WS_CLOSE_POLICY_VIOLATION
    ) {
      return
    }

    const delay = reconnectBackoffMs(reconnectAttempt)
    reconnectAttempt += 1
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      void attemptReconnect()
    }, delay)
  }

  const reconnectNow = () => {
    if (cancelled || !bootCompleted) {
      return
    }

    reconnectTimer = clearTimer(reconnectTimer)
    reconnectAttempt = 0
    reconnectErrorNotified = false

    if (!gatewayOpen()) {
      void attemptReconnect()
    }
  }

  const gateway = new SpiritAgentGateway()

  const { syncTools, invalidateConnection } = createRunnerToolsSync({
    gateway,
    getTools: () => desktop.runnerGetTools(),
    isAlive: () => !cancelled && Boolean(isAuthCurrent?.())
  })

  setPrimaryGateway(gateway)
  const { resumeSessionHistory } = createHostHistoryRecovery(gateway, () => !cancelled)

  const offStageOwner = $presentation.listen(state => {
    if (state.stageOwner === 'sprite' && gatewayOpen()) {
      startAutonomyProvision()
    } else {
      stopAutonomyProvision()
    }
  })

  const offState = gateway.onState(st => {
    if (cancelled) {
      return
    }

    if (st !== 'open') {
      invalidateConnection()
    }

    reportPrimaryGatewayState(st)
    desktop.gatewayBroadcastState(st)

    if (st === 'open') {
      reconnectAttempt = 0
      lastReconnectError = null
      reconnectErrorNotified = false
      reconnectTimer = clearTimer(reconnectTimer)
      graceTimer = clearTimer(graceTimer)
      // 重推打扰档位与本地时区，覆盖离线期间尚未上云的变化。
      syncDisturbanceTier()
      syncTimezone(gateway)
      void refreshDesktopVideos()
      void fetchSlashCommandMeta()

      if ($presentation.get().stageOwner === 'sprite') {
        startAutonomyProvision()
      }

      clearVfx('sleep_zzz')

      if (bootCompleted) {
        const cur = $spriteState.get()

        if (cur === 'disconnected') {
          setSpriteState('idle', { force: true })
        }
      }

      void resumeSessionHistory()
    } else if (bootCompleted && (st === 'closed' || st === 'error')) {
      // 断连的回合不会再收到 complete/error ——正在合成/播放的语音条在此中止并释放。
      cancelVoiceBar()
      stopSpeaking()

      // 安排断连宽限状态；超时则固定进入 disconnected，重连前不再做额外升级。
      if (graceTimer === null) {
        const isForeground = document.visibilityState === 'visible'
        const graceMs = isForeground ? 3000 : 30000
        graceTimer = setTimeout(() => {
          graceTimer = null
          setSpriteState('disconnected')
          emitVfx('sleep_zzz', { nx: 0.5, ny: 0.05 })
        }, graceMs)
      }

      scheduleReconnect()
    }
  })

  const offEvent = gateway.onEvent(event => {
    if (cancelled || !isAuthCurrent?.()) {
      return
    }

    handleGatewayEvent(event)

    if (!isDeviceCommandEvent(event.type)) {
      desktop.gatewayBroadcastEvent(event)
    }
  })

  const offRpcDispatch = desktop.onGatewayRpcDispatch(req => {
    void (async () => {
      try {
        const result = await gateway.request(req.method, req.params)
        desktop.gatewayRpcReply({ id: req.id, ok: true, result })
      } catch (error) {
        const message = errorMessage(error)
        desktop.gatewayRpcReply({
          id: req.id,
          ok: false,
          error: error instanceof SpiritAgentRpcError ? { code: error.code, data: error.data, message } : { message }
        })
      }
    })()
  })

  const offPowerResume = desktop.onPowerResume(() => reconnectNow())

  const onOnline = () => reconnectNow()

  const onVisible = () => {
    if (document.visibilityState === 'visible') {
      reconnectNow()
    }
  }

  window.addEventListener('online', onOnline)
  document.addEventListener('visibilitychange', onVisible)

  const offRunnerStatus = desktop.onRunnerStatus(ev => {
    if (gateway.connectionState === 'open') {
      void syncTools(ev.type === 'running' || ev.type === 'runner_ready' ? ev.tools : [])
    }
  })

  async function boot(): Promise<void> {
    try {
      await connectOnce()

      if (cancelled) {
        return
      }

      clearDesktopBootFailure()
      bootCompleted = true
    } catch (err) {
      if (!cancelled) {
        const message = errorMessage(err)
        failDesktopBoot(message)
        notifyError(err, getStrings().boot.errors.desktopBootFailed)
      }
    }
  }

  void boot()

  return () => {
    cancelled = true
    reconnectTimer = clearTimer(reconnectTimer)
    graceTimer = clearTimer(graceTimer)
    window.removeEventListener('online', onOnline)
    document.removeEventListener('visibilitychange', onVisible)
    offPowerResume()
    offRpcDispatch()
    offState()
    offEvent()
    desktop.gatewayBroadcastState('closed')
    offRunnerStatus()
    offStageOwner()
    stopAutonomyProvision()
    tearDownPrimaryGateway()
  }
}
