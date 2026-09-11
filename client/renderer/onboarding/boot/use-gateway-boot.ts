import { useEffect, useRef } from 'react'

import {
  $chatMessageList,
  $chatSessionId,
  cancelVoiceBar,
  hydrateChatMessages,
  hydrateSessionSettings,
  openMainSession,
  setChatSession
} from '@/chat'
import {
  $effectiveTier,
  $spriteState,
  clearVfx,
  emitVfx,
  pushEffectiveDisturbanceTier,
  setSpriteState,
  startAutonomyProvision,
  stopAutonomyProvision,
  stopSpeaking
} from '@/companion'
import { type GatewayEvent } from '@/shared/lib/gateway-protocol'
import { resolveGatewayWsUrl } from '@/shared/lib/gateway-ws-url'
import { log } from '@/shared/lib/log'
import { reconnectBackoffMs } from '@/shared/lib/reconnect'
import { fetchSlashCommandMeta } from '@/shared/lib/slash-commands'
import { SpiritAgentGateway } from '@/shared/spiritagent'
import { logout } from '@/shared/store/auth'
import { reportPrimaryGatewayState, setPrimaryGateway, tearDownPrimaryGateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { getStrings } from '@/shared/strings'
import type { SessionResumeResponse } from '@/shared/types/spiritagent'

import { applyDesktopBootProgress, completeDesktopBoot, failDesktopBoot, setDesktopBootStep } from '../boot-store'

// 后端对鉴权失败（token 过期 / 被吊销）使用 WS close 1008——
// 此时触发登出，而不是用无效 token 不断重连。
const WS_CLOSE_POLICY_VIOLATION = 1008

// 每次（重）开后重推一次生效档位（含活动覆盖）：离线期间的档位变化可能尚未
// 上云，重推保证后端闸门尽快收敛到最新生效值。即发即忘。
function syncDisturbanceTier(): void {
  const tier = $effectiveTier.get()

  if (!tier) {
    return
  }

  pushEffectiveDisturbanceTier(tier)
}

// 每次连接上报本地 IANA 时区：后端的夜间批处理与互动统计都按用户本地日聚合，
// 缺这一行时整个夜间流水线（画像/整理/规划/日记）会静默跳过。即发即忘。
function syncTimezone(gateway: SpiritAgentGateway): void {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone

  if (!timezone) {
    return
  }

  void gateway.request('companion.set_timezone', { timezone }).catch(() => {})
}

async function syncRunnerTools(gateway: SpiritAgentGateway): Promise<void> {
  const desktop = window.spiritagent

  if (!desktop?.runnerGetTools) {
    return
  }

  try {
    const tools = await desktop.runnerGetTools()

    const names = tools
      .map((t: { function?: { name?: string }; name?: string }) => t.function?.name || t.name)
      .filter(Boolean) as string[]

    const hasFileTools = names.includes('read_file') || names.includes('list_directory')

    if (!hasFileTools) {
      log.warn('gateway-boot', 'tools.sync: LLM will lack file tools in this session')
    }

    if (tools.length > 0) {
      const res = await gateway.request<{ count: number }>('tools.sync', { tools })
      log.info(
        'gateway-boot',
        `tools.sync: synced ${res.count || tools.length} runner tools to gateway (hasFileTools=${hasFileTools})`
      )
    }
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error)
    log.error('gateway-boot', `tools.sync failed: ${msg}`)
  }
}

interface GatewayBootOptions {
  handleGatewayEvent: (event: GatewayEvent) => void
  onGatewayReady: (gateway: SpiritAgentGateway | null) => void
}

export function useGatewayBoot({ handleGatewayEvent, onGatewayReady }: GatewayBootOptions): void {
  const callbacksRef = useRef({ handleGatewayEvent, onGatewayReady })

  callbacksRef.current = { handleGatewayEvent, onGatewayReady }

  useEffect(() => {
    let cancelled = false
    const desktop = window.spiritagent

    if (!desktop) {
      failDesktopBoot('Desktop IPC bridge is unavailable.')

      return () => void (cancelled = true)
    }

    // macOS 睡眠会静默丢掉渲染端的 WebSocket。后端 Python 进程仍在跑，
    // 但唤醒时没人重开 socket，应用永远卡在 "Starting…"。一旦初次启动成功，
    // 我们就把任何非 open 状态视为可恢复，按退避重连，
    // 并在唤醒相关的 OS / 浏览器信号（电源恢复、网络上线、窗口变为可见）触发
    // 时主动 nudge 一次重连。
    let bootCompleted = false
    let bootOverlayDismissed = false
    let reconnecting = false
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let graceTimer: ReturnType<typeof setTimeout> | null = null
    let reconnectAttempt = 0
    let lastReconnectError: Error | null = null
    let reconnectErrorNotified = false

    const dismissOverlayOnce = () => {
      if (bootOverlayDismissed) {
        return
      }

      bootOverlayDismissed = true
      completeDesktopBoot()
    }

    const gatewayOpen = () => gateway.connectionState === 'open'

    const clearReconnectTimer = () => {
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
    }

    const clearGraceTimer = () => {
      if (graceTimer !== null) {
        clearTimeout(graceTimer)
        graceTimer = null
      }
    }

    const attemptReconnect = async () => {
      if (cancelled || reconnecting || gatewayOpen()) {
        return
      }

      reconnecting = true

      try {
        const conn = await desktop.getConnection()

        if (cancelled) {
          return
        }

        // 重连前重新生成 WS URL。OAuth 票据是一次性的且 TTL 很短，
        // 所以缓存 conn.wsUrl 里那条票据在初次启动后的每次重连都已失效——
        // 复用只会换来一条神秘的"无法连接到网关"。resolveGatewayWsUrl 会签发
        // 新票据（OAuth 模式下会抛 reauth 错误，而不是拿着过期票据硬连）。
        // local/token 网关的 URL 携带的是长效 token，重新签发是廉价的空操作。
        const wsUrl = await resolveGatewayWsUrl(desktop, conn)
        await gateway.connect(wsUrl)

        if (cancelled) {
          return
        }

        void syncRunnerTools(gateway)
        reconnectAttempt = 0
        lastReconnectError = null
        reconnectErrorNotified = false
      } catch (error) {
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
      if (cancelled || reconnecting || reconnectTimer !== null || gatewayOpen()) {
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

      clearReconnectTimer()
      reconnectAttempt = 0
      reconnectErrorNotified = false

      if (!gatewayOpen()) {
        void attemptReconnect()
      }
    }

    const offBootProgress = desktop.onBootProgress(payload => applyDesktopBootProgress(payload))
    void desktop
      .getBootProgress()
      .then(snapshot => applyDesktopBootProgress(snapshot))
      .catch(() => undefined)

    setDesktopBootStep({
      phase: 'renderer.boot',
      message: getStrings().boot.steps.startingDesktopConnection,
      progress: 6
    })

    const gateway = new SpiritAgentGateway()
    callbacksRef.current.onGatewayReady(gateway)
    setPrimaryGateway(gateway)

    const offState = gateway.onState(st => {
      reportPrimaryGatewayState(st)
      window.spiritagent?.gatewayBroadcastState?.(st)

      if (st === 'open') {
        reconnectAttempt = 0
        lastReconnectError = null
        reconnectErrorNotified = false
        clearReconnectTimer()
        clearGraceTimer()
        // 重推打扰档位与本地时区，覆盖离线期间尚未上云的变化。
        syncDisturbanceTier()
        syncTimezone(gateway)
        void fetchSlashCommandMeta()
        startAutonomyProvision()

        // 断连阶段挂的 sleep_zzz 气泡需要主动清掉，否则即便精灵已经"醒来"
        // 头顶的 z 字符仍会停留到自然 expiry。
        clearVfx('sleep_zzz')

        // 正常的唤醒后重连不会再次调用 completeDesktopBoot()，
        // 所以这里在再次 open 后把启动进度浮层收掉——否则它会一直挂着。
        // 初次启动时是 no-op。
        if (bootCompleted) {
          dismissOverlayOnce()
          // 重连后"打起精神"：如果之前表达过 disconnected 降级，就回到 idle
          // （DESIGN §6.5）。纯视觉表现（动画状态机切回 idle 姿态/微动），保持静默回神。
          const cur = $spriteState.get()

          if (cur === 'disconnected') {
            setSpriteState('idle', { force: true })
          }
        }

        // 重新挂载会话，避免下一次 prompt.submit 触发"找不到 session"。
        // 初次启动时 $chatSessionId 从 localStorage 恢复为上次活跃会话；重连时
        // 则是内存里的现行会话。后端每次 WS 断开都会清空内存里的 runtime_sessions，
        // session.resume 从持久化的 DB 会话记录里把运行时重新派生出来。恢复的
        // 会话已被删除（或换了账号）时 resume 报错，清掉持久化 id 并回退主会话。
        const sid = $chatSessionId.get()

        const syncMountSeq = (res: SessionResumeResponse) => {
          if (typeof res.current_seq === 'number') {
            gateway.resetSeq(res.current_seq)
          }
        }

        const hasMessages = $chatMessageList.get().length > 0
        const lastSeq = hasMessages && gateway.lastReceivedSeq > 0 ? gateway.lastReceivedSeq : undefined

        if (sid) {
          void gateway
            .request<SessionResumeResponse>('session.resume', {
              session_id: sid,
              ...(lastSeq !== undefined ? { last_seq: lastSeq } : {})
            })
            .then(res => {
              syncMountSeq(res)

              if (!res.resumed || !hasMessages) {
                if (Array.isArray(res.messages)) {
                  hydrateChatMessages(res.messages, res.info)
                }
              } else if (res.info) {
                hydrateSessionSettings(res.info)
              }
            })
            .catch(() => {
              setChatSession(null)
              void openMainSession(syncMountSeq)
            })
        } else {
          void openMainSession(syncMountSeq)
        }
      } else if (bootCompleted && (st === 'closed' || st === 'error')) {
        // 断连的回合不会再收到 complete/error——正在合成/播放的语音条在此中止并释放。
        cancelVoiceBar()
        stopSpeaking()

        if (st === 'closed' && gateway.lastCloseCode === WS_CLOSE_POLICY_VIOLATION) {
          void logout()

          return
        }

        // 安排断连宽限状态；超时则固定进入 disconnected，重连前不再做额外升级。
        if (graceTimer === null) {
          const isForeground = document.visibilityState === 'visible'
          const graceMs = isForeground ? 3000 : 30000
          graceTimer = setTimeout(() => {
            graceTimer = null
            setSpriteState('disconnected')
            // DESIGN §6.5：犯困/走神 → 挂载 sleep_zzz 气泡；重连后由 onState='open'
            // 分支清掉气泡并平滑切回 idle 状态。
            emitVfx('sleep_zzz', { nx: 0.5, ny: 0.05 })
          }, graceMs)
        }

        scheduleReconnect()
      }
    })

    const offEvent = gateway.onEvent(event => {
      callbacksRef.current.handleGatewayEvent(event)

      if (event.type !== 'tool.call') {
        window.spiritagent?.gatewayBroadcastEvent?.(event)
      }
    })

    const offRpcDispatch = window.spiritagent?.onGatewayRpcDispatch?.(req => {
      void (async () => {
        try {
          const result = await gateway.request(req.method, req.params)
          window.spiritagent?.gatewayRpcReply?.({ id: req.id, ok: true, result })
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          window.spiritagent?.gatewayRpcReply?.({ id: req.id, ok: false, error: message })
        }
      })()
    })

    const offPowerResume = desktop.onPowerResume?.(() => reconnectNow())

    const onOnline = () => reconnectNow()

    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        reconnectNow()
      }
    }

    window.addEventListener('online', onOnline)
    document.addEventListener('visibilitychange', onVisible)

    const offRunnerStatus = desktop.onRunnerStatus?.(ev => {
      if (ev.type === 'running' || ev.type === 'runner_ready') {
        if (gateway.connectionState === 'open') {
          void syncRunnerTools(gateway)
        }
      }
    })

    async function boot(): Promise<void> {
      try {
        const conn = await desktop.getConnection()

        if (cancelled) {
          return
        }

        setDesktopBootStep({
          phase: 'renderer.gateway.connect',
          message: getStrings().boot.steps.connectingGateway,
          progress: 95
        })
        const wsUrl = await resolveGatewayWsUrl(desktop, conn)
        await gateway.connect(wsUrl)

        if (cancelled) {
          return
        }

        void syncRunnerTools(gateway)
        dismissOverlayOnce()
        bootCompleted = true
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : String(err)
          failDesktopBoot(message)
          notifyError(err, getStrings().boot.errors.desktopBootFailed)
        }
      }
    }

    void boot()

    return () => {
      cancelled = true
      clearReconnectTimer()
      clearGraceTimer()
      window.removeEventListener('online', onOnline)
      document.removeEventListener('visibilitychange', onVisible)
      offPowerResume?.()
      offRpcDispatch?.()
      offState()
      offEvent()
      window.spiritagent?.gatewayBroadcastState?.('closed')
      offRunnerStatus?.()
      offBootProgress()
      stopAutonomyProvision()
      callbacksRef.current.onGatewayReady(null)
      tearDownPrimaryGateway()
    }
  }, [])
}
