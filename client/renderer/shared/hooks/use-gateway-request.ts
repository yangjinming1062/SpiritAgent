import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useRef } from 'react'

import { resolveGatewayWsUrl } from '../lib/gateway-ws-url'
import type { SpiritAgentGateway } from '../spiritagent'
import { $gateway, $gatewayState, type SpiritAgentGatewayLike } from '../store/gateway'

import { useLatestRef } from './use-latest-ref'

// 匹配所有由网络断开、服务端断线、会话失效或 OAuth 鉴权过期引发的重试错误家族
const RECONNECTABLE_GATEWAY_ERROR =
  /not connected|connection closed|connection reset|socket closed|session expired|session not found|reauth|unauthorized|token expired|ticket expired/i

export interface UseGatewayRequestResult {
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
}

export function useGatewayRequest(): UseGatewayRequestResult {
  const gatewayState = useStore($gatewayState)
  const gatewayRef = useRef<SpiritAgentGateway | SpiritAgentGatewayLike | null>(null)
  const gatewayStateRef = useLatestRef(gatewayState)
  const reconnectingRef = useRef<Promise<SpiritAgentGateway | SpiritAgentGatewayLike | null> | null>(null)

  // 跟踪当前活动的 gateway，让出站请求与 overlay props 都打到当前 socket 上。
  useEffect(
    () =>
      $gateway.subscribe((gateway: SpiritAgentGateway | SpiritAgentGatewayLike | null) => {
        gatewayRef.current = gateway
      }),
    []
  )

  const ensureGatewayOpen = useCallback(async () => {
    const existing = gatewayRef.current

    if (!existing) {
      return null
    }

    if (gatewayStateRef.current === 'open') {
      return existing
    }

    if (reconnectingRef.current) {
      return reconnectingRef.current
    }

    reconnectingRef.current = (async () => {
      const desktop = window.spiritagent

      if (!desktop) {
        return null
      }

      try {
        const conn = await desktop.getConnection()
        // 重连前重新签发 WS URL。OAuth 票据是一次性且短期的，
        // 所以缓存 conn.wsUrl 里的票据在此已死；
        // resolveGatewayWsUrl() 在 OAuth 模式下会抛 reauth 错误，
        // 而不是拿着过期票据硬连。
        const wsUrl = await resolveGatewayWsUrl(desktop, conn)
        await existing.connect?.(wsUrl)

        return existing
      } catch {
        return null
      } finally {
        reconnectingRef.current = null
      }
    })()

    return reconnectingRef.current
  }, [gatewayStateRef])

  const requestGateway = useCallback(
    async <T>(method: string, params: Record<string, unknown> = {}) => {
      const gateway = gatewayRef.current

      if (!gateway) {
        throw new Error('SpiritAgent gateway unavailable')
      }

      try {
        return await gateway.request<T>(method, params)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)

        if (!RECONNECTABLE_GATEWAY_ERROR.test(message)) {
          throw error
        }

        // 远端网关每次连接都会重新签发一次性 OAuth 票据，
        // 因此过期票据会表现为"connection closed"，
        // 我们走一次本地重连路径重试一次再放弃。
        const recovered = await ensureGatewayOpen()

        if (!recovered) {
          throw error
        }

        return recovered.request<T>(method, params)
      }
    },
    [ensureGatewayOpen]
  )

  return { requestGateway }
}
