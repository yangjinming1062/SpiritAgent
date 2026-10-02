import { useEffect } from 'react'

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
import {
  $chatMessageList,
  $chatSessionId,
  $chatTurnInFlight,
  hydrateChatMessages,
  hydrateSessionSettings,
  loadLocalSessionHistory,
  openMainSession,
  SessionHistoryChangedError,
  setChatSession,
  syncSessionHistory
} from '@/modules/conversation'
import { cancelVoiceBar, stopSpeaking } from '@/modules/speech'
import { errorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { reconnectBackoffMs } from '@/shared/lib/reconnect'
import { fetchSlashCommandMeta } from '@/shared/lib/slash-commands'
import { SpiritAgentGateway } from '@/shared/spiritagent'
import { reportPrimaryGatewayState, setPrimaryGateway, tearDownPrimaryGateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { getStrings } from '@/shared/strings'
import type { SessionResumeResponse } from '@/shared/types/spiritagent'

import { clearDesktopBootFailure, failDesktopBoot } from './boot-store'
import { handleGatewayEvent } from './gateway-event-router'
import { isDeviceCommandEvent } from './gateway-event-util'

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

// 空工具表撤销新调用资格，已派发调用仍按原结果与超时规则收尾。
async function syncRunnerTools(gateway: SpiritAgentGateway, isCurrent: () => boolean, revoke: boolean): Promise<void> {
  const desktop = window.spiritagent

  if (!desktop?.runnerGetTools) {
    return
  }

  try {
    const tools = revoke ? [] : await desktop.runnerGetTools()

    if (!isCurrent()) {
      return
    }

    const hasFileTools = tools.some(tool => tool.name === 'read_file' || tool.name === 'list_directory')

    if (!hasFileTools) {
      log.warn('gateway-boot', 'tools.sync: LLM will lack file tools in this session')
    }

    const res = await gateway.request<{ count: number }>('tools.sync', { tools, skill_scope_version: 1 })
    log.info('gateway-boot', `tools.sync: synced ${res.count} runner tools to gateway (hasFileTools=${hasFileTools})`)
  } catch (error) {
    const msg = errorMessage(error)
    log.error('gateway-boot', `tools.sync failed: ${msg}`)
  }
}

export function useGatewayBoot(sessionId: string): void {
  useEffect(() => {
    let cancelled = false
    let toolsSyncGeneration = 0
    const desktop = window.spiritagent

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

    const syncTools = (revoke: boolean = false): Promise<void> => {
      const generation = ++toolsSyncGeneration

      return syncRunnerTools(gateway, () => !cancelled && generation === toolsSyncGeneration && gatewayOpen(), revoke)
    }

    const attemptReconnect = async () => {
      if (cancelled || reconnecting || gatewayOpen() || gateway.lastCloseCode === WS_CLOSE_POLICY_VIOLATION) {
        return
      }

      reconnecting = true

      try {
        const wsUrl = await desktop.getGatewayWsUrl()

        if (cancelled) {
          return
        }

        await gateway.connect(wsUrl)

        if (cancelled) {
          return
        }

        void syncTools()
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
    setPrimaryGateway(gateway)

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
        toolsSyncGeneration++
      }

      reportPrimaryGatewayState(st)
      desktop.gatewayBroadcastState?.(st)

      if (st === 'open') {
        reconnectAttempt = 0
        lastReconnectError = null
        reconnectErrorNotified = false
        reconnectTimer = clearTimer(reconnectTimer)
        graceTimer = clearTimer(graceTimer)
        // 重推打扰档位与本地时区，覆盖离线期间尚未上云的变化。
        syncDisturbanceTier()
        syncTimezone(gateway)
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

        // 恢复服务端会话并同步历史；失败仅回退仍被选中的会话。
        const sid = $chatSessionId.get()

        const syncMountSeq = (res: { current_seq?: number }) => {
          if (typeof res.current_seq === 'number') {
            gateway.resetSeq(res.current_seq)
          }
        }

        if (sid) {
          void (async () => {
            try {
              const local = await loadLocalSessionHistory(sid)

              if (cancelled || $chatSessionId.get() !== sid) {
                return
              }

              const hasMessages = $chatMessageList.get().length > 0

              // 本地秒开：先渲染缓存再后台增量追上。网关 seq 不重置到缓存值——后端重启后 seq 从低值重新增长，陈旧高水位会把实时帧当重复丢弃。
              if (local && !hasMessages) {
                hydrateChatMessages(local.messages, local.info)
              }

              // last_seq 只在聊天列表是活数据（重连）时发；缓存不追踪实时回合，冷启动一律走 after_id 增量，否则服务端按陈旧水位重放会重复追加。
              const synced = await syncSessionHistory({
                lastSeq: hasMessages && gateway.lastReceivedSeq > 0 ? gateway.lastReceivedSeq : undefined,
                sessionId: sid,
                request: body => gateway.request<SessionResumeResponse>('session.resume', { session_id: sid, ...body })
              })

              if (cancelled || $chatSessionId.get() !== sid) {
                return
              }

              if (synced.currentSeq > 0) {
                gateway.resetSeq(synced.currentSeq)
              }

              const liveHasMessages = $chatMessageList.get().length > 0

              if (!liveHasMessages || synced.kind !== 'noop') {
                hydrateChatMessages(synced.messages, synced.info)

                // 历史水合会重置流式气泡，也要同步独立的服务端回合状态。
                if (typeof synced.info?.running === 'boolean') {
                  $chatTurnInFlight.set(synced.info.running)
                }
              } else if (synced.info) {
                hydrateSessionSettings(synced.info)
              }
            } catch (error) {
              if (cancelled || $chatSessionId.get() !== sid) {
                return
              }

              if (error instanceof SessionHistoryChangedError) {
                log.warn('gateway-boot', 'History is changing; keeping current session:', error)

                return
              }

              setChatSession(null)
              void openMainSession(syncMountSeq)
            }
          })()
        } else {
          void openMainSession(syncMountSeq)
        }
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
      handleGatewayEvent(event)

      if (!isDeviceCommandEvent(event.type)) {
        desktop.gatewayBroadcastEvent?.(event)
      }
    })

    const offRpcDispatch = desktop.onGatewayRpcDispatch?.(req => {
      void (async () => {
        try {
          const result = await gateway.request(req.method, req.params)
          desktop.gatewayRpcReply?.({ id: req.id, ok: true, result })
        } catch (error) {
          const message = errorMessage(error)
          desktop.gatewayRpcReply?.({ id: req.id, ok: false, error: message })
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
      if (gateway.connectionState === 'open') {
        void syncTools(ev.type !== 'running' && ev.type !== 'runner_ready')
      }
    })

    async function boot(): Promise<void> {
      try {
        const wsUrl = await desktop.getGatewayWsUrl()

        if (cancelled) {
          return
        }

        await gateway.connect(wsUrl)

        if (cancelled) {
          return
        }

        void syncTools()
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
      offPowerResume?.()
      offRpcDispatch?.()
      offState()
      offEvent()
      desktop.gatewayBroadcastState?.('closed')
      offRunnerStatus?.()
      offStageOwner()
      stopAutonomyProvision()
      tearDownPrimaryGateway()
    }
  }, [sessionId])
}
