import type { GatewayEvent, RpcRequest } from '../../../../shared/gateway'

/** 会话历史磁盘快照；实时交付位置由主进程网关持有。 */
export interface SessionHistorySnapshot {
  info?: Record<string, unknown>
  messages: unknown[]
  writtenAt: number
}

export type DesktopGatewayState = 'closed' | 'connecting' | 'error' | 'idle' | 'open'

export interface DesktopGatewaySnapshot {
  state: DesktopGatewayState
  error: string | null
  hasConnected: boolean
  activeToolCount: number
  recovery: 'replayed' | 'snapshot_required' | null
  streamId: string | null
}

export type DesktopGatewayEvent = GatewayEvent

export type DesktopApiResult<T = unknown> =
  | { ok: true; value: T }
  | {
      ok: false
      status: number | null
      error: { code: string; message: string; details: unknown; request_id: string }
    }

export interface DesktopPresenceRequest {
  authSessionId: string
  signal: RpcRequest<'companion.signal'>
}
