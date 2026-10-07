import { isRecord } from '@/shared/lib/is-record'
import { log } from '@/shared/lib/log'
import { safeJsonParse } from '@/shared/lib/safe-json'
import type { DesktopGatewayEvent, DesktopGatewayState } from '@ipc/contracts'

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
  method?: string
  params?: {
    payload?: unknown
    seq?: number
    stream_id?: string
    session_id?: string
    type: string
    [key: string]: unknown
  }
  result?: unknown
}

/** 字段缺省或类型符合。 */
function optionalType(obj: Record<string, unknown>, key: string, type: 'number' | 'string'): boolean {
  return !(key in obj) || typeof obj[key] === type
}

function parseJsonRpcFrame(raw: string): JsonRpcFrame | null {
  const v = safeJsonParse<unknown>(raw, null)

  if (!isRecord(v) || !optionalType(v, 'method', 'string')) {
    return null
  }

  if ('id' in v && v.id !== null && typeof v.id !== 'string' && typeof v.id !== 'number') {
    return null
  }

  if ('error' in v) {
    const err = v.error

    if (!isRecord(err) || !optionalType(err, 'code', 'number') || !optionalType(err, 'message', 'string')) {
      return null
    }
  }

  if ('params' in v) {
    const params = v.params

    if (
      !isRecord(params) ||
      typeof params.type !== 'string' ||
      !optionalType(params, 'seq', 'number') ||
      !optionalType(params, 'session_id', 'string') ||
      !optionalType(params, 'stream_id', 'string')
    ) {
      return null
    }
  }

  return v as unknown as JsonRpcFrame
}

// JSON-RPC 2.0 标准错误码 + SpiritAgent 扩展码——与后端 components/constants.py 的 JSONRPC_* 保持同步，消费方可按 err.code 分支而无需解析 err.message。
export enum SpiritAgentRpcErrorCode {
  ParseError = -32700,
  InvalidRequest = -32600,
  MethodNotFound = -32601,
  InvalidParams = -32602,
  InternalError = -32603,
  // Slash 命令扩展错误码：与 backend/components/constants.py JSONRPC_SLASH_* 对齐。
  SlashConfirmRequired = -32001,
  SlashBusy = -32002,
  SlashGeneric = -32003,
  TurnBusy = -32004
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

const SNAPSHOT_METHODS = new Set(['session.resume', 'session.get_main', 'session.create', 'session.fork'])

// 这些事件已由历史和在途快照表达；设备指令与未持久化终态必须继续交付。
const SNAPSHOT_EVENTS = new Set([
  'session.state',
  'message.start',
  'message.delta',
  'message.reasoning.delta',
  'message.break',
  'message.bubble',
  'message.persisted',
  'message.complete',
  'message.edited',
  'message.deleted',
  'tool.start',
  'tool.complete'
])

const DEFAULT_REQUEST_TIMEOUT_MS = 30_000
// 休眠唤醒后重连不得永久卡在 'connecting'（会禁用输入框并卡住 "Starting SpiritAgent..."）；握手超时应落到 'error' 让调用方重试。
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
  private _streamId = ''
  private eventHolds = 0
  private heldEvents: GatewayEvent[] = []
  private flushEventsTimer: ReturnType<typeof setTimeout> | null = null
  private snapshotPositions = new Map<string, { streamId: string; seq: number }>()
  private _ackTimer: ReturnType<typeof setTimeout> | null = null
  private _lastMessageAt = 0
  private _heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private readonly eventHandlers = new Set<(event: GatewayEvent) => void>()
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

  get streamId(): string {
    return this._streamId
  }

  resetSeq(seq = 0, streamId = this._streamId): void {
    if (streamId !== this._streamId) {
      this._streamId = streamId
      this._lastReceivedSeq = 0
      this.snapshotPositions.clear()
    }

    this._lastReceivedSeq = Math.max(this._lastReceivedSeq, seq)
  }

  private finishEventHold(): void {
    this.eventHolds -= 1

    if (this.eventHolds !== 0 || this.flushEventsTimer !== null) {
      return
    }

    // 先让等待 RPC 的调用方同步水合，再处理快照之后的新帧。
    this.flushEventsTimer = setTimeout(() => {
      this.flushEventsTimer = null

      if (this.eventHolds !== 0) {
        return
      }

      const events = this.heldEvents
      this.heldEvents = []

      for (const event of events) {
        const position = event.session_id ? this.snapshotPositions.get(event.session_id) : undefined
        const commandResult = event.type === 'command.result' && isRecord(event.payload) ? event.payload.result : null

        const replacesHistory =
          isRecord(commandResult) && commandResult.status === 'ok' && commandResult.hydrate === true

        if (
          position &&
          event.stream_id === position.streamId &&
          typeof event.seq === 'number' &&
          event.seq <= position.seq &&
          (SNAPSHOT_EVENTS.has(event.type) || replacesHistory)
        ) {
          continue
        }

        this.dispatchEvent(event)
      }

      this.scheduleAck()
    }, 0)
  }

  private acceptSnapshot(value: unknown): void {
    if (!isRecord(value) || typeof value.stream_id !== 'string' || typeof value.current_seq !== 'number') {
      return
    }

    this.resetSeq(value.current_seq, value.stream_id)

    if (!value.resumed && typeof value.session_id === 'string' && Array.isArray(value.messages)) {
      const previous = this.snapshotPositions.get(value.session_id)
      this.snapshotPositions.set(value.session_id, {
        streamId: value.stream_id,
        seq: Math.max(previous?.seq ?? 0, value.current_seq)
      })
      // 宿主与所有 IPC 代理都应用同一快照，才能安全丢弃快照内已有的旧回合帧。
      this.dispatchEvent({
        type: 'session.snapshot',
        session_id: value.session_id,
        stream_id: value.stream_id,
        payload: value
      })
    }
  }

  ackSeq(seq = this._lastReceivedSeq): void {
    if (seq > 0 && this.socket?.readyState === WebSocket.OPEN) {
      void this.request('session.ack', { seq }).catch(() => {})
    }
  }

  private scheduleAck(): void {
    if (this._ackTimer !== null || this.eventHolds > 0 || this.flushEventsTimer !== null) {
      return
    }

    this._ackTimer = setTimeout(() => {
      this._ackTimer = null

      if (this.eventHolds === 0 && this.flushEventsTimer === null) {
        this.ackSeq()
      }
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
      this.heldEvents = []
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
    this.heldEvents = []

    if (this.flushEventsTimer !== null) {
      clearTimeout(this.flushEventsTimer)
      this.flushEventsTimer = null
    }

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

  onEvent(handler: (event: GatewayEvent) => void): () => void {
    this.eventHandlers.add(handler)

    return () => this.eventHandlers.delete(handler)
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
    const snapshot = SNAPSHOT_METHODS.has(method)

    if (snapshot) {
      this.eventHolds += 1
    }

    if (method === 'session.resume' && params.last_seq && !params.stream_id && this._streamId) {
      params = { ...params, stream_id: this._streamId }
    }

    return new Promise<T>((resolve, reject) => {
      const pending: PendingCall = {
        reject: error => {
          reject(error)

          if (snapshot) {
            this.finishEventHold()
          }
        },
        resolve: value => {
          try {
            if (snapshot) {
              this.acceptSnapshot(value)
            }

            resolve(value as T)
          } catch (error) {
            reject(error instanceof Error ? error : new Error(String(error)))
          } finally {
            if (snapshot) {
              this.finishEventHold()
            }
          }
        }
      }

      if (timeoutMs > 0) {
        pending.timer = setTimeout(() => {
          if (this.pending.delete(id)) {
            pending.reject(new Error(`request timed out: ${method}`))
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
        pending.reject(error instanceof Error ? error : new Error(String(error)))
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

    const streamId = frame.params?.stream_id

    if (typeof streamId === 'string' && streamId !== this._streamId) {
      this.resetSeq(0, streamId)
    }

    const seq = frame.params?.seq

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
      if (this.eventHolds > 0 || this.flushEventsTimer !== null) {
        this.heldEvents.push(frame.params)
      } else {
        this.dispatchEvent(frame.params)
      }
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
    for (const handler of this.eventHandlers) {
      try {
        handler(event)
      } catch (error) {
        log.error('gateway', 'Event consumer failed', error)
      }
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
