import type { DesktopGatewayEvent, DesktopGatewayState } from '@ipc/contracts'

type GatewayEventName =
  | 'command.result'
  | 'compress.completed'
  | 'companion.action.catalog_changed'
  | 'companion.action.job_updated'
  | 'companion.action.play_requested'
  | 'companion.mood'
  | 'companion.diary.upserted'
  | 'companion.message'
  | 'companion.moment.comment'
  | 'companion.moment.created'
  | 'companion.outfit.updated'
  | 'companion.video.activated'
  | 'companion.video.failed'
  | 'companion.video.ready'
  | 'companion.scene.updated'
  | 'companion.scene.activated'
  | 'channel.peer_request'
  | 'channel.status'
  | 'error'
  | 'message.break'
  | 'message.complete'
  | 'message.voice'
  | 'message.media'
  | 'message.deleted'
  | 'message.edited'
  | 'message.delta'
  | 'message.persisted'
  | 'message.reasoning.delta'
  | 'message.start'
  | 'system.notification'
  | 'tool.call'
  | 'tool.complete'
  | 'tool.start'
  | 'video_gen.completed'
  | 'video_gen.failed'
  | 'avatar.regenerated'
  | (string & {})

/** Slash 命令结果 payload：与 docs/PROTOCOL.md「事件路由」 `command.result` 事件载荷一致。 */
export interface SlashCommandResultPayload {
  /** 实际执行的主名（不带 /）。 */
  command: string
  /** 命令执行结果。 */
  result: {
    status: 'ok' | 'error'
    message: string
    payload?: Record<string, unknown> | null
    /** 为 true 时客户端用 payload.messages 替换本地消息列表。 */
    hydrate?: boolean
  }
}

export type GatewayEvent<P = unknown> = DesktopGatewayEvent<P>

export type ConnectionState = DesktopGatewayState

type PendingCall = {
  reject: (error: Error) => void
  resolve: (value: unknown) => void
  timer?: ReturnType<typeof setTimeout>
}

interface JsonRpcFrame {
  error?: { code?: number; data?: unknown; message?: string }
  id?: null | number | string
  jsonrpc?: string
  method?: string
  params?: {
    payload?: unknown
    seq?: number
    session_id?: string
    type: string
    [key: string]: unknown
  }
  result?: unknown
  seq?: number
}

function parseJsonRpcFrame(raw: string): JsonRpcFrame | null {
  let value: unknown

  try {
    value = JSON.parse(raw)
  } catch {
    return null
  }

  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }

  const v = value as Record<string, unknown>

  if ('jsonrpc' in v && typeof v.jsonrpc !== 'string') {
    return null
  }

  if ('id' in v) {
    const rawId = v.id

    if (rawId !== null && typeof rawId !== 'string' && typeof rawId !== 'number') {
      return null
    }
  }

  if ('method' in v && typeof v.method !== 'string') {
    return null
  }

  if ('error' in v) {
    const e = v.error

    if (typeof e !== 'object' || e === null || Array.isArray(e)) {
      return null
    }

    const err = e as Record<string, unknown>

    if ('code' in err && typeof err.code !== 'number') {
      return null
    }

    if ('message' in err && typeof err.message !== 'string') {
      return null
    }
  }

  if ('params' in v) {
    const p = v.params

    if (typeof p !== 'object' || p === null || Array.isArray(p)) {
      return null
    }

    const paramsObj = p as Record<string, unknown>

    if (typeof paramsObj.type !== 'string') {
      return null
    }

    if ('seq' in paramsObj && typeof paramsObj.seq !== 'number') {
      return null
    }

    if ('session_id' in paramsObj && typeof paramsObj.session_id !== 'string') {
      return null
    }
  }

  if ('seq' in v && typeof v.seq !== 'number') {
    return null
  }

  return v as unknown as JsonRpcFrame
}

// JSON-RPC 2.0 标准错误码 + SpiritAgent 扩展码——与后端 components/constants.py 的 JSONRPC_* 保持同步，
// 消费方可按 err.code 分支而无需解析 err.message
export enum SpiritAgentRpcErrorCode {
  ParseError = -32700,
  InvalidRequest = -32600,
  MethodNotFound = -32601,
  InvalidParams = -32602,
  InternalError = -32603,
  // Slash 命令扩展错误码：与 backend/components/constants.py JSONRPC_SLASH_* 对齐。
  SlashConfirmRequired = -32001,
  SlashBusy = -32002,
  SlashGeneric = -32003
}

export class SpiritAgentRpcError extends Error {
  readonly code: number
  readonly data?: unknown

  constructor(code: number, message: string, data?: unknown) {
    super(message)
    this.name = 'SpiritAgentRpcError'
    this.code = code
    this.data = data
  }
}

const ANY = '*'
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000
// A reconnect after sleep/wake must not hang forever in 'connecting' (which
// keeps the composer disabled and stuck on "Starting SpiritAgent..."). If the open
// handshake doesn't land in this window, fail to 'error' so callers can retry.
const CONNECT_TIMEOUT_MS = 15_000

const CLOSED_ERROR_MESSAGE = 'SpiritAgent gateway connection closed'
const CONNECT_ERROR_MESSAGE = 'Could not connect to SpiritAgent gateway'
const NOT_CONNECTED_ERROR_MESSAGE = 'SpiritAgent gateway is not connected'

// 空闲 15s 发 session.ping；30s 无任何帧则判定半开连接，close(4000) 触发重连
const HEARTBEAT_INTERVAL_MS = 15_000
const HEARTBEAT_DEADLINE_MS = 30_000

export class JsonRpcGatewayClient {
  private nextId = 0
  private pending = new Map<number, PendingCall>()
  private socket: WebSocket | null = null
  private state: ConnectionState = 'idle'
  private _lastCloseCode: number | null = null
  private _lastReceivedSeq = 0
  private _ackTimer: ReturnType<typeof setTimeout> | null = null
  private _lastMessageAt = 0
  private _heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private readonly eventHandlers = new Map<string, Set<(event: GatewayEvent) => void>>()
  private readonly stateHandlers = new Set<(state: ConnectionState) => void>()

  get connectionState(): ConnectionState {
    return this.state
  }

  /** Close code from the last WebSocket close event, or null if never closed. */
  get lastCloseCode(): number | null {
    return this._lastCloseCode
  }

  /** Monotonic sequence ID of the last received event frame from backend. */
  get lastReceivedSeq(): number {
    return this._lastReceivedSeq
  }

  resetSeq(seq = 0): void {
    this._lastReceivedSeq = seq
  }

  ackSeq(seq = this._lastReceivedSeq): void {
    if (seq > 0 && this.socket?.readyState === WebSocket.OPEN) {
      void this.request('session.ack', { seq }).catch(() => {})
    }
  }

  private scheduleAck(): void {
    if (this._ackTimer !== null) {
      return
    }

    this._ackTimer = setTimeout(() => {
      this._ackTimer = null
      this.ackSeq()
    }, 1000)
  }

  async connect(wsUrl: string): Promise<void> {
    if (this.socket?.readyState === WebSocket.OPEN || this.state === 'connecting') {
      return
    }

    this.setState('connecting')

    const socket = new WebSocket(wsUrl)
    this.socket = socket

    socket.addEventListener('message', message => {
      if (this.socket !== socket) {
        return
      }

      this.handleMessage(message.data)
    })

    socket.addEventListener('close', (event: CloseEvent) => {
      if (this.socket !== socket) {
        return
      }

      this.stopHeartbeat()
      this._lastCloseCode = event.code
      this.socket = null
      this.setState('closed')
      this.rejectAllPending(new Error(CLOSED_ERROR_MESSAGE))
    })

    await new Promise<void>((resolve, reject) => {
      let settled = false
      let timer: ReturnType<typeof setTimeout> | undefined

      const cleanup = () => {
        if (timer !== undefined) {
          clearTimeout(timer)
        }

        socket.removeEventListener('open', onOpen)
        socket.removeEventListener('error', onError)
      }

      const onOpen = () => {
        if (settled || this.socket !== socket) {
          return
        }

        settled = true
        cleanup()
        this._lastMessageAt = Date.now()
        this.startHeartbeat()
        this.setState('open')
        resolve()
      }

      const onError = () => {
        if (settled || this.socket !== socket) {
          return
        }

        settled = true
        cleanup()
        this.setState('error')
        reject(new Error(CONNECT_ERROR_MESSAGE))
      }

      socket.addEventListener('open', onOpen, { once: true })
      socket.addEventListener('error', onError, { once: true })

      timer = setTimeout(() => {
        if (settled) {
          return
        }

        settled = true
        cleanup()

        // 丢弃半开 socket，避免下次 connect() 在僵尸 'connecting' 状态上短路
        if (this.socket === socket) {
          try {
            socket.close()
          } catch {}

          this.socket = null
        }

        this.setState('error')
        reject(new Error(CONNECT_ERROR_MESSAGE))
      }, CONNECT_TIMEOUT_MS)
    })
  }

  close(): void {
    if (this._ackTimer !== null) {
      clearTimeout(this._ackTimer)
      this._ackTimer = null
    }

    this.stopHeartbeat()

    if (this.socket) {
      this.socket.close()
      this.socket = null
    }

    this.rejectAllPending(new Error(CLOSED_ERROR_MESSAGE))
    this.setState('closed')
  }

  on<P = unknown>(type: GatewayEventName, handler: (event: GatewayEvent<P>) => void): () => void {
    let handlers = this.eventHandlers.get(type)

    if (!handlers) {
      handlers = new Set()
      this.eventHandlers.set(type, handlers)
    }

    handlers.add(handler as (event: GatewayEvent) => void)

    return () => handlers?.delete(handler as (event: GatewayEvent) => void)
  }

  onEvent(handler: (event: GatewayEvent) => void): () => void {
    return this.on(ANY as GatewayEventName, handler)
  }

  onState(handler: (state: ConnectionState) => void): () => void {
    this.stateHandlers.add(handler)
    handler(this.state)

    return () => this.stateHandlers.delete(handler)
  }

  request<T>(method: string, params: Record<string, unknown> = {}, timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS): Promise<T> {
    const socket = this.socket

    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error(NOT_CONNECTED_ERROR_MESSAGE))
    }

    const id = ++this.nextId

    return new Promise<T>((resolve, reject) => {
      const pending: PendingCall = {
        reject,
        resolve: value => resolve(value as T)
      }

      if (timeoutMs > 0) {
        pending.timer = setTimeout(() => {
          if (this.pending.delete(id)) {
            reject(new Error(`request timed out: ${method}`))
          }
        }, timeoutMs)
      }

      this.pending.set(id, pending)

      try {
        socket.send(
          JSON.stringify({
            jsonrpc: '2.0',
            id,
            method,
            params
          })
        )
      } catch (error) {
        this.clearPending(id)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  private startHeartbeat(): void {
    if (this._heartbeatTimer !== null) {
      return
    }

    this._heartbeatTimer = setInterval(() => {
      const socket = this.socket

      if (!socket || socket.readyState !== WebSocket.OPEN) {
        this.stopHeartbeat()

        return
      }

      const elapsed = Date.now() - this._lastMessageAt

      // 超时无帧 → 半开连接，主动 close 触发既有重连链路
      if (elapsed > HEARTBEAT_DEADLINE_MS) {
        try {
          socket.close(4000, 'heartbeat')
        } catch {}

        return
      }

      // 空闲足够久 → 发轻量 ping 保活 NAT 映射；错误由 close 事件兜底
      if (elapsed > HEARTBEAT_INTERVAL_MS) {
        this.request('session.ping').catch(() => {})
      }
    }, HEARTBEAT_INTERVAL_MS)
  }

  private stopHeartbeat(): void {
    if (this._heartbeatTimer !== null) {
      clearInterval(this._heartbeatTimer)
      this._heartbeatTimer = null
    }
  }

  private handleMessage(raw: unknown): void {
    this._lastMessageAt = Date.now()

    if (typeof raw !== 'string') {
      return
    }

    const frame = parseJsonRpcFrame(raw)

    if (!frame) {
      return
    }

    const seq = frame.params?.seq ?? frame.seq

    if (typeof seq === 'number') {
      if (seq < this._lastReceivedSeq) {
        return
      }

      if (seq === this._lastReceivedSeq) {
        this.scheduleAck()

        return
      }

      this._lastReceivedSeq = seq
      this.scheduleAck()
    }

    if (frame.id !== undefined && frame.id !== null) {
      // 本端请求 ID 均为数字，其他 ID 的响应没有对应的在途请求。
      if (typeof frame.id !== 'number') {
        return
      }

      const call = this.pending.get(frame.id)

      if (!call) {
        return
      }

      this.clearPending(frame.id)

      if (frame.error) {
        const code = typeof frame.error.code === 'number' ? frame.error.code : SpiritAgentRpcErrorCode.InternalError
        call.reject(new SpiritAgentRpcError(code, frame.error.message || 'SpiritAgent RPC failed', frame.error.data))
      } else {
        call.resolve(frame.result)
      }

      return
    }

    if (frame.method === 'event' && frame.params?.type) {
      this.dispatchEvent(frame.params)
    }
  }

  private clearPending(id: number): void {
    const call = this.pending.get(id)

    if (call?.timer) {
      clearTimeout(call.timer)
    }

    this.pending.delete(id)
  }

  private dispatchEvent(event: GatewayEvent): void {
    for (const handler of this.eventHandlers.get(event.type) ?? []) {
      handler(event)
    }

    for (const handler of this.eventHandlers.get(ANY) ?? []) {
      handler(event)
    }
  }

  private rejectAllPending(error: Error): void {
    for (const [id, call] of this.pending) {
      if (call.timer) {
        clearTimeout(call.timer)
      }

      call.reject(error)
      this.pending.delete(id)
    }
  }

  private setState(state: ConnectionState): void {
    if (this.state === state) {
      return
    }

    this.state = state

    for (const handler of this.stateHandlers) {
      handler(state)
    }
  }
}
