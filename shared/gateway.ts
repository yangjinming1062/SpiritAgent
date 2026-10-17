import { type ApiOperations, EVENT_POLICIES, type GatewayEventPayloads, type RpcOperations } from './api.generated'
import { ApiError } from './errors'
export { isJsonValue } from './errors'
export type { JsonValue } from './errors'

export type RpcMethod = keyof RpcOperations
export type RpcRequest<Method extends RpcMethod> = RpcOperations[Method]['request']
export type RpcResponse<Method extends RpcMethod> = RpcOperations[Method]['response']
export type SessionSnapshot = ApiOperations['sessions.snapshot']['response'] & { snapshot_read_id?: number }
export type GatewayRecovery = RpcResponse<'gateway.resume'>

let nextSnapshotReadId = 0

export type GatewayNetworkEvent = {
  [Event in keyof GatewayEventPayloads]: {
    type: Event
    payload: GatewayEventPayloads[Event]
    seq?: number
    stream_id?: string
    session_id?: string
  }
}[keyof GatewayEventPayloads]

export type GatewayEvent =
  | GatewayNetworkEvent
  | {
      type: 'session.snapshot'
      payload: SessionSnapshot
      session_id: string
      stream_id?: string
      seq?: number
    }

export interface GatewaySocketEvent {
  readonly type: string
  readonly data?: unknown
  readonly code?: number
}

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

export type ConnectionState = 'closed' | 'connecting' | 'error' | 'idle' | 'open'

export class SpiritAgentRpcError extends Error {
  constructor(
    readonly code: number,
    message: string,
    readonly data?: unknown
  ) {
    super(message)
    this.name = 'SpiritAgentRpcError'
  }
}

interface PendingCall {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}

interface SnapshotRead {
  events: GatewayNetworkEvent[]
  promise: Promise<SessionSnapshot>
  streamId?: string | null
  draining: boolean
  dispatching: boolean
  drained: Promise<void>
  finishDrain: () => void
}

export function eventsAfterSnapshot(events: GatewayEvent[], snapshot: SessionSnapshot): GatewayEvent[] {
  return events.filter(event => {
    if (
      event.type === 'session.snapshot' ||
      event.stream_id !== snapshot.stream_id ||
      event.seq === undefined ||
      snapshot.current_seq === null ||
      event.seq > snapshot.current_seq
    ) {
      return true
    }
    if (event.type === 'command.result' && !event.payload.result.hydrate) {
      return true
    }
    return EVENT_POLICIES[event.type].recovery !== 'session'
  })
}

export class JsonRpcGatewayClient {
  private nextId = 0
  private readonly pending = new Map<number, PendingCall>()
  private socket: GatewaySocket | null = null
  private state: ConnectionState = 'idle'
  private closeCode: number | null = null
  private receivedSeq = 0
  private activeStreamId = ''
  private recovery: GatewayRecovery | null = null
  private recovering = false
  private heldEvents: GatewayNetworkEvent[] = []
  private readonly snapshots = new Map<string, SnapshotRead>()
  private readonly baselines = new Map<string, string>()
  private ackTimer: ReturnType<typeof setTimeout> | undefined
  private heartbeatTimer: ReturnType<typeof setInterval> | undefined
  private lastMessageAt = 0
  private lifetime = 0
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
    return this.closeCode
  }
  get streamId(): string {
    return this.activeStreamId
  }
  get sequence(): number {
    return this.receivedSeq
  }
  get lastRecovery(): GatewayRecovery | null {
    return this.recovery
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

  synchronize(sessionId: string, load: (streamId?: string) => Promise<SessionSnapshot>): Promise<SessionSnapshot> {
    const lifetime = this.lifetime
    const pending = this.snapshots.get(sessionId)
    if (pending) {
      if (
        pending.draining &&
        (pending.dispatching || (this.state === 'open' && pending.streamId !== this.activeStreamId))
      ) {
        return pending.drained.then(() => {
          if (lifetime !== this.lifetime) {
            throw new ApiError(null, {
              code: 'cancelled',
              message: 'Snapshot owner changed',
              details: null,
              request_id: ''
            })
          }
          return this.synchronize(sessionId, load)
        })
      }
      return pending.promise
    }
    let finishDrain: (() => void) | undefined
    const drained = new Promise<void>(resolve => {
      finishDrain = resolve
    })
    const events: GatewayNetworkEvent[] = []
    const snapshotReadId = ++nextSnapshotReadId
    const promise = Promise.resolve().then(async () => {
      let snapshot: SessionSnapshot | undefined
      try {
        snapshot = await load(this.state === 'open' ? this.activeStreamId : undefined)
        if (this.state === 'open' && snapshot.stream_id !== this.activeStreamId) {
          snapshot = await load(this.state === 'open' ? this.activeStreamId : undefined)
        }
        if (lifetime !== this.lifetime) {
          throw new ApiError(null, {
            code: 'cancelled',
            message: 'Snapshot owner changed',
            details: null,
            request_id: ''
          })
        }
        snapshot = { ...snapshot, snapshot_read_id: snapshotReadId }
        const current = this.snapshots.get(sessionId)
        if (current?.promise === promise) {
          current.streamId = snapshot.stream_id
        }
        this.dispatchEvent({
          type: 'session.snapshot',
          session_id: sessionId,
          payload: snapshot,
          ...(snapshot.stream_id ? { stream_id: snapshot.stream_id } : {})
        })
        return snapshot
      } finally {
        const read = this.snapshots.get(sessionId)
        if (read?.promise === promise) {
          read.draining = true
        }
        setTimeout(() => {
          const current = this.snapshots.get(sessionId)
          if (current?.promise !== promise || lifetime !== this.lifetime) {
            finishDrain?.()
            return
          }
          for (const event of events) {
            const hasBaseline = snapshot
              ? snapshot.stream_id === event.stream_id
              : this.baselines.get(sessionId) === event.stream_id
            if (EVENT_POLICIES[event.type].baseline && !hasBaseline) {
              continue
            }
            if (!snapshot || eventsAfterSnapshot([event], snapshot).length) {
              current.dispatching = true
              try {
                this.dispatchEvent(event)
              } finally {
                current.dispatching = false
              }
            }
          }
          if (snapshot?.stream_id === this.activeStreamId) {
            this.baselines.set(sessionId, this.activeStreamId)
          }
          this.snapshots.delete(sessionId)
          finishDrain?.()
          this.scheduleAck()
        }, 0)
      }
    })
    this.snapshots.set(sessionId, {
      events,
      promise,
      draining: false,
      dispatching: false,
      drained,
      finishDrain: () => finishDrain?.()
    })
    return promise
  }

  async connect(url: string): Promise<void> {
    if (this.socket?.readyState === 1 || this.state === 'connecting') {
      return
    }
    this.closeCode = null
    this.recovering = true
    this.heldEvents = []
    this.setState('connecting')
    let socket: GatewaySocket
    try {
      socket = this.createSocket(url)
    } catch (error) {
      this.recovering = false
      this.setState('error')
      throw error
    }
    this.socket = socket
    socket.addEventListener('message', event => {
      if (this.socket === socket) {
        this.handleMessage(event.data)
      }
    })
    socket.addEventListener('close', event => {
      if (this.socket !== socket) {
        return
      }
      this.closeCode = event.code ?? null
      this.socket = null
      this.recovering = false
      this.heldEvents = []
      this.stopTimers()
      this.rejectPending(new Error('SpiritAgent gateway connection closed'))
      this.setState('closed')
    })
    try {
      await new Promise<void>((resolve, reject) => {
        const finish = (error?: Error): void => {
          clearTimeout(timer)
          socket.removeEventListener('open', opened)
          socket.removeEventListener('error', failed)
          socket.removeEventListener('close', failed)
          if (error) {
            reject(error)
          } else {
            resolve()
          }
        }
        const opened = (): void => finish()
        const failed = (): void => finish(new Error('Could not connect to SpiritAgent gateway'))
        const timer = setTimeout(failed, 15_000)
        socket.addEventListener('open', opened)
        socket.addEventListener('error', failed)
        socket.addEventListener('close', failed)
      })
      const recovery = await this.request('gateway.resume', {
        ...(this.activeStreamId ? { stream_id: this.activeStreamId, last_seq: this.receivedSeq } : {})
      })
      if (this.socket !== socket) {
        throw new Error('SpiritAgent gateway connection changed')
      }
      if (this.activeStreamId !== recovery.stream_id) {
        this.receivedSeq = 0
      }
      if (recovery.recovery === 'snapshot_required') {
        this.baselines.clear()
      }
      this.activeStreamId = recovery.stream_id
      this.recovery = recovery
      this.recovering = false
      const held = this.heldEvents
      this.heldEvents = []
      for (const event of held) {
        this.acceptEvent(event)
      }
      if (recovery.recovery === 'snapshot_required') {
        this.receivedSeq = Math.max(this.receivedSeq, recovery.current_seq)
      }
      this.lastMessageAt = Date.now()
      this.startHeartbeat()
      this.setState('open')
      this.scheduleAck()
    } catch (error) {
      if (this.socket === socket) {
        this.socket = null
        socket.close()
        this.recovering = false
        this.heldEvents = []
        this.rejectPending(new Error('SpiritAgent gateway connection closed'))
        this.setState('error')
      }
      throw error
    }
  }

  close(): void {
    this.lifetime++
    for (const read of this.snapshots.values()) {
      read.finishDrain()
    }
    this.snapshots.clear()
    this.baselines.clear()
    this.stopTimers()
    const socket = this.socket
    this.socket = null
    socket?.close()
    this.recovering = false
    this.heldEvents = []
    this.rejectPending(new Error('SpiritAgent gateway connection closed'))
    this.setState('closed')
  }

  request<Method extends RpcMethod>(method: Method, params: RpcRequest<Method>): Promise<RpcResponse<Method>> {
    const socket = this.socket
    if (!socket || socket.readyState !== 1) {
      return Promise.reject(new Error('SpiritAgent gateway is not connected'))
    }
    const id = ++this.nextId
    return new Promise<RpcResponse<Method>>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error('Gateway control timed out: ' + method))
      }, 30_000)
      this.pending.set(id, { timer, reject, resolve: value => resolve(value as RpcResponse<Method>) })
      try {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }))
      } catch (error) {
        clearTimeout(timer)
        this.pending.delete(id)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== 'string') {
      return
    }
    let frame: unknown
    try {
      frame = JSON.parse(raw)
    } catch {
      return
    }
    if (!isRecord(frame) || frame.jsonrpc !== '2.0') {
      return
    }
    this.lastMessageAt = Date.now()
    if (typeof frame.id === 'number') {
      const pending = this.pending.get(frame.id)
      if (!pending) {
        return
      }
      clearTimeout(pending.timer)
      this.pending.delete(frame.id)
      if (isRecord(frame.error)) {
        pending.reject(
          new SpiritAgentRpcError(
            typeof frame.error.code === 'number' ? frame.error.code : -32603,
            typeof frame.error.message === 'string' ? frame.error.message : 'Gateway control failed',
            frame.error.data
          )
        )
      } else if ('result' in frame) {
        pending.resolve(frame.result)
      } else {
        pending.reject(new Error('Invalid gateway response'))
      }
      return
    }
    const params = frame.params
    if (
      frame.method !== 'event' ||
      !isRecord(params) ||
      typeof params.type !== 'string' ||
      !Object.hasOwn(EVENT_POLICIES, params.type) ||
      !isRecord(params.payload) ||
      typeof params.stream_id !== 'string' ||
      typeof params.seq !== 'number' ||
      !Number.isSafeInteger(params.seq) ||
      params.seq < 1 ||
      (params.session_id !== undefined && typeof params.session_id !== 'string')
    ) {
      return
    }
    const event = params as unknown as GatewayNetworkEvent
    if (this.recovering) {
      this.heldEvents.push(event)
    } else {
      this.acceptEvent(event)
    }
  }

  private acceptEvent(event: GatewayNetworkEvent): void {
    if (event.stream_id !== this.activeStreamId || event.seq === undefined) {
      return
    }
    if (event.seq <= this.receivedSeq) {
      this.scheduleAck()
      return
    }
    if (this.state === 'open' && event.seq > this.receivedSeq + 1) {
      this.socket?.close(4001, 'event_gap')
      return
    }
    this.receivedSeq = event.seq
    const snapshot = event.session_id ? this.snapshots.get(event.session_id) : undefined
    if (snapshot) {
      snapshot.events.push(event)
    } else if (
      !event.session_id ||
      !EVENT_POLICIES[event.type].baseline ||
      this.baselines.get(event.session_id) === this.activeStreamId
    ) {
      this.dispatchEvent(event)
    }
    this.scheduleAck()
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

  private scheduleAck(): void {
    if (this.ackTimer || this.recovering || this.snapshots.size || !this.receivedSeq) {
      return
    }
    this.ackTimer = setTimeout(() => {
      this.ackTimer = undefined
      if (!this.recovering && !this.snapshots.size && this.state === 'open') {
        void this.request('gateway.ack', { seq: this.receivedSeq }).catch(() => {})
      }
    }, 1000)
  }

  private startHeartbeat(): void {
    this.heartbeatTimer = setInterval(() => {
      if (Date.now() - this.lastMessageAt > 30_000) {
        this.socket?.close(4000, 'heartbeat')
      } else if (Date.now() - this.lastMessageAt > 15_000) {
        void this.request('gateway.ping', {}).catch(() => {})
      }
    }, 15_000)
  }

  private stopTimers(): void {
    clearTimeout(this.ackTimer)
    clearInterval(this.heartbeatTimer)
    this.ackTimer = undefined
    this.heartbeatTimer = undefined
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer)
      pending.reject(error)
    }
    this.pending.clear()
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

export class GatewayConnection {
  readonly client: JsonRpcGatewayClient
  private enabled = true
  private blocked = false
  private attempt = 0
  private connecting: Promise<void> | null = null
  private timer: ReturnType<typeof setTimeout> | undefined

  constructor(
    private readonly options: {
      createSocket: (url: string) => GatewaySocket
      url: (streamId?: string) => Promise<string>
      onExpired: () => void
      onError: (error: unknown) => void
    }
  ) {
    this.client = new JsonRpcGatewayClient(options.createSocket, options.onError)
    this.client.onState(state => {
      if (this.enabled && (state === 'closed' || state === 'error')) {
        if (this.client.lastCloseCode === 1008 || this.client.lastCloseCode === 4403) {
          this.blocked = true
          this.options.onError(
            new ApiError(403, {
              code: 'gateway_policy_rejected',
              message: '实时连接被拒绝，请检查连接设置后重试',
              details: null,
              request_id: ''
            })
          )
        } else {
          this.schedule()
        }
      }
    })
  }

  connect(): Promise<void> {
    if (!this.enabled || this.blocked || this.client.connectionState === 'open') {
      return Promise.resolve()
    }
    if (this.connecting) {
      return this.connecting
    }
    const task = (async () => {
      try {
        const url = await this.options.url(this.client.streamId || undefined)
        if (!this.enabled) {
          return
        }
        await this.client.connect(url)
        this.attempt = 0
        clearTimeout(this.timer)
        this.timer = undefined
      } catch (error) {
        if (!this.enabled) {
          return
        }
        this.options.onError(error)
        if (error instanceof ApiError && error.isLoginExpired) {
          this.blocked = true
          this.options.onExpired()
        } else {
          this.schedule()
        }
      }
    })()
    this.connecting = task
    void task.finally(() => {
      if (this.connecting === task) {
        this.connecting = null
      }
    })
    return task
  }

  reconnect(): void {
    this.blocked = false
    this.attempt = 0
    clearTimeout(this.timer)
    this.timer = undefined
    void this.connect()
  }

  close(): void {
    this.enabled = false
    clearTimeout(this.timer)
    this.timer = undefined
    this.client.close()
  }

  private schedule(): void {
    if (!this.enabled || this.blocked || this.timer) {
      return
    }
    this.timer = setTimeout(
      () => {
        this.timer = undefined
        void this.connect()
      },
      Math.min(1000 * 2 ** Math.min(this.attempt++, 5), 30_000)
    )
  }
}
