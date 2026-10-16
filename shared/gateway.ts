import { type ApiSchemas, RPC_POLICIES, type RpcOperations } from './api.generated'

export type RpcMethod = keyof RpcOperations
export type RpcRequest<K extends RpcMethod> = RpcOperations[K]['request']
export type RpcResponse<K extends RpcMethod> = RpcOperations[K]['response']
export type RpcRequester = <K extends RpcMethod>(method: K, params: RpcRequest<K>) => Promise<RpcResponse<K>>

export function isJsonValue(value: unknown): value is ApiSchemas['JsonValue'] {
  return (
    value === null ||
    typeof value === 'string' ||
    typeof value === 'boolean' ||
    (typeof value === 'number' && Number.isFinite(value)) ||
    (Array.isArray(value) ? value.every(isJsonValue) : isRecord(value) && Object.values(value).every(isJsonValue))
  )
}

export interface GatewaySocketEvent {
  readonly type: string
  readonly data?: unknown
  readonly code?: number
}

/** 浏览器与 Electron net.WebSocket 共用的最小传输边界。 */
export interface GatewaySocket {
  readonly readyState: number
  send(data: string): void
  close(code?: number, reason?: string): void
  addEventListener(type: string, listener: (event: GatewaySocketEvent) => void): void
  removeEventListener(type: string, listener: (event: GatewaySocketEvent) => void): void
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export interface GatewayEvent {
  type: string
  payload?: unknown
  seq?: number
  stream_id?: string
  session_id?: string
}

export type ConnectionState = 'closed' | 'connecting' | 'error' | 'idle' | 'open'

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
  let v: unknown
  try {
    v = JSON.parse(raw)
  } catch {
    return null
  }

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

// 错误码与 backend/infrastructure/constants.py 的 JSONRPC_* 同步。
export enum SpiritAgentRpcErrorCode {
  ParseError = -32700,
  InvalidRequest = -32600,
  MethodNotFound = -32601,
  InvalidParams = -32602,
  InternalError = -32603,
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

// 这些事件已由历史和在途快照表达；设备指令与未持久化终态必须继续交付。
const SNAPSHOT_EVENTS = new Set([
  'message.replaced',
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
// 握手限时覆盖半开连接，休眠恢复后允许重试。
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
  private socket: GatewaySocket | null = null
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

  constructor(
    private readonly createSocket: (url: string) => GatewaySocket,
    private readonly onConsumerError: (error: unknown) => void
  ) {}

  get connectionState(): ConnectionState {
    return this.state
  }

  get lastCloseCode(): number | null {
    return this._lastCloseCode
  }

  private resetSeq(seq: number, streamId: string): void {
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
      // 各视图应用同一快照后，才能丢弃已被快照覆盖的帧。
      this.dispatchEvent({
        type: 'session.snapshot',
        session_id: value.session_id,
        stream_id: value.stream_id,
        payload: value
      })
    }
  }

  private ackSeq(): void {
    const seq = this._lastReceivedSeq
    if (seq > 0 && this.socket?.readyState === 1) {
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
    if (this.socket?.readyState === 1 || this.state === 'connecting') {
      return
    }

    this._lastCloseCode = null
    this.setState('connecting')

    let socket: GatewaySocket
    try {
      socket = this.createSocket(wsUrl)
    } catch (error) {
      this.setState('error')
      throw error
    }
    this.socket = socket

    socket.addEventListener('message', message => {
      if (this.socket !== socket) {
        return
      }

      this.handleMessage(message.data)
    })

    socket.addEventListener('close', event => {
      if (this.socket !== socket) {
        return
      }

      this.stopHeartbeat()
      this.heldEvents = []
      this._lastCloseCode = event.code ?? null
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
        socket.removeEventListener('close', onHandshakeClose)
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
        if (this.socket === socket) {
          this.socket = null
          try {
            socket.close()
          } catch {}
        }
        this.setState('error')
        reject(new Error(CONNECT_ERROR_MESSAGE))
      }

      const onHandshakeClose = () => {
        if (settled) {
          return
        }
        settled = true
        cleanup()
        reject(new Error(CONNECT_ERROR_MESSAGE))
      }

      socket.addEventListener('close', onHandshakeClose)
      socket.addEventListener('open', onOpen)
      socket.addEventListener('error', onError)

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

  request<K extends RpcMethod>(method: K, params: RpcRequest<K>): Promise<RpcResponse<K>> {
    const timeoutMs = RPC_POLICIES[method].wait_for_model ? 0 : DEFAULT_REQUEST_TIMEOUT_MS
    const socket = this.socket

    if (!socket || socket.readyState !== 1) {
      return Promise.reject(new Error(NOT_CONNECTED_ERROR_MESSAGE))
    }

    const id = ++this.nextId
    const snapshot = method === 'session.resume'

    if (snapshot) {
      this.eventHolds += 1
    }

    return new Promise<RpcResponse<K>>((resolve, reject) => {
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

            resolve(value as RpcResponse<K>)
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

      if (!socket || socket.readyState !== 1) {
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
        this.request('session.ping', {}).catch(() => {})
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
        this.onConsumerError(error)
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
      try {
        handler(state)
      } catch (error) {
        this.onConsumerError(error)
      }
    }
  }
}
