import type { RemoteSession } from './types'

export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message)
  }
}

export class RemoteApi {
  private csrf = ''
  private revision = 0
  private controllers = new Set<AbortController>()
  onExpired: () => void = () => {}

  authenticate(session: RemoteSession): void {
    this.clear()
    this.csrf = session.csrf_token
  }

  clear(): void {
    this.revision++
    this.csrf = ''
    this.controllers.forEach(controller => controller.abort())
    this.controllers.clear()
  }

  async request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
    const revision = this.revision
    const controller = new AbortController()
    this.controllers.add(controller)
    const timer = setTimeout(() => controller.abort(), 180_000)
    const headers = new Headers()

    if (method !== 'GET' && this.csrf) {
      headers.set('X-CSRF-Token', this.csrf)
    }

    let payload: BodyInit | undefined

    if (body instanceof FormData) {
      payload = body
    } else if (body !== undefined) {
      headers.set('Content-Type', 'application/json')
      payload = JSON.stringify(body)
    }

    try {
      const response = await fetch(path, {
        method,
        body: payload,
        headers,
        credentials: 'same-origin',
        signal: controller.signal
      })

      const value: unknown = response.status === 204 ? null : await response.json()

      if (revision !== this.revision) {
        throw new DOMException('账户已切换', 'AbortError')
      }

      if (!response.ok) {
        if (response.status === 401 && this.csrf) {
          this.onExpired()
        }

        const detail = record(value) ? value.detail : undefined

        const message =
          typeof detail === 'string'
            ? detail
            : record(detail) && typeof detail.error === 'string'
              ? detail.error
              : `请求失败（${response.status}）`

        throw new ApiError(message, response.status)
      }

      return value as T
    } finally {
      clearTimeout(timer)
      this.controllers.delete(controller)
    }
  }
}

export interface GatewayEvent {
  type: string
  payload?: unknown
  session_id?: string
  seq?: number
}

interface Pending {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}

export class RemoteGateway {
  private socket: WebSocket | null = null
  private stopped = false
  private connection = 0
  private connecting: Promise<void> | null = null
  private nextId = 0
  private pending = new Map<number, Pending>()
  private retryTimer: ReturnType<typeof setTimeout> | undefined
  private heartbeat: ReturnType<typeof setInterval> | undefined
  private ackTimer: ReturnType<typeof setTimeout> | undefined
  private sequence = 0
  private lastFrame = Date.now()
  private eventHandlers = new Set<(event: GatewayEvent) => void>()
  private stateHandlers = new Set<(open: boolean) => void>()
  open = false

  constructor(
    private api: RemoteApi,
    private invalidated: () => void
  ) {}

  subscribeEvent(handler: (event: GatewayEvent) => void): () => void {
    this.eventHandlers.add(handler)

    return () => this.eventHandlers.delete(handler)
  }

  subscribeState(handler: (open: boolean) => void): () => void {
    this.stateHandlers.add(handler)

    return () => this.stateHandlers.delete(handler)
  }

  connect(): Promise<void> {
    if (
      this.stopped ||
      this.socket?.readyState === WebSocket.CONNECTING ||
      this.socket?.readyState === WebSocket.OPEN
    ) {
      return Promise.resolve()
    }

    if (!this.connecting) {
      this.connecting = this.openSocket().finally(() => {
        this.connecting = null
      })
    }

    return this.connecting
  }

  private async openSocket(): Promise<void> {
    const connection = ++this.connection

    try {
      const { ticket } = await this.api.request<{ ticket: string }>('/api/remote/ws-ticket', 'POST', {})

      if (this.stopped || connection !== this.connection) {
        return
      }

      const url = new URL('/api/remote/ws', location.origin)
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      url.searchParams.set('ticket', ticket)
      const socket = new WebSocket(url)
      this.socket = socket
      const timeout = setTimeout(() => socket.close(), 15_000)

      socket.onopen = () => {
        clearTimeout(timeout)

        if (this.stopped || this.socket !== socket) {
          socket.close()

          return
        }
        this.sequence = 0
        this.lastFrame = Date.now()
        this.setOpen(true)
        this.heartbeat = setInterval(() => {
          if (Date.now() - this.lastFrame > 75_000) {
            socket.close()
          } else {
            void this.request('session.ping').catch(() => socket.close())
          }
        }, 25_000)
      }

      socket.onmessage = frame => {
        if (this.socket === socket && !this.stopped) {
          this.receive(frame.data)
        }
      }
      socket.onerror = () => socket.close()

      socket.onclose = event => {
        clearTimeout(timeout)

        if (this.socket !== socket) {
          return
        }

        this.socket = null
        this.drop()

        if (event.code === 4401 || event.code === 4403) {
          this.invalidated()
        } else {
          this.reconnect()
        }
      }
    } catch {
      this.reconnect()
    }
  }

  close(): void {
    this.stopped = true
    this.connection++
    clearTimeout(this.retryTimer)
    this.socket?.close()
    this.socket = null
    this.drop()
    this.eventHandlers.clear()
    this.stateHandlers.clear()
  }

  request<T>(method: string, params: Record<string, unknown> = {}, timeout = 30_000): Promise<T> {
    const socket = this.socket

    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error('连接已断开，正在重新连接'))
    }

    const id = ++this.nextId

    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error('尚未确认是否受理，请重连后查看会话'))
      }, timeout)

      this.pending.set(id, {
        resolve: value => resolve(value as T),
        reject,
        timer
      })

      try {
        socket.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }))
      } catch (failure) {
        clearTimeout(timer)
        this.pending.delete(id)
        reject(failure instanceof Error ? failure : new Error('连接已断开'))
      }
    })
  }

  private receive(raw: unknown): void {
    if (typeof raw !== 'string') {
      return
    }

    this.lastFrame = Date.now()
    let frame: unknown

    try {
      frame = JSON.parse(raw)
    } catch {
      return
    }

    if (!record(frame)) {
      return
    }

    if (typeof frame.id === 'number') {
      const pending = this.pending.get(frame.id)

      if (pending) {
        clearTimeout(pending.timer)
        this.pending.delete(frame.id)

        if (record(frame.error)) {
          pending.reject(new Error(typeof frame.error.message === 'string' ? frame.error.message : '请求失败'))
        } else {
          pending.resolve(frame.result)
        }
      }

      return
    }

    if (frame.method !== 'event' || !record(frame.params) || typeof frame.params.type !== 'string') {
      return
    }

    const params = frame.params

    if (typeof params.seq === 'number') {
      const duplicate = params.seq <= this.sequence
      this.sequence = Math.max(this.sequence, params.seq)
      if (this.ackTimer === undefined) {
        this.ackTimer = setTimeout(() => {
          this.ackTimer = undefined
          void this.request('session.ack', { seq: this.sequence }).catch(() => {})
        }, 150)
      }

      if (duplicate) {
        return
      }
    }

    const event: GatewayEvent = {
      type: frame.params.type,
      payload: params.payload,
      seq: typeof params.seq === 'number' ? params.seq : undefined,
      session_id: typeof params.session_id === 'string' ? params.session_id : undefined
    }

    this.eventHandlers.forEach(handler => handler(event))
  }

  private reconnect(): void {
    if (!this.stopped) {
      clearTimeout(this.retryTimer)
      this.retryTimer = setTimeout(() => {
        void this.connect()
      }, 2500)
    }
  }

  private setOpen(open: boolean): void {
    this.open = open
    this.stateHandlers.forEach(handler => handler(open))
  }

  private drop(): void {
    clearInterval(this.heartbeat)
    clearTimeout(this.ackTimer)
    this.ackTimer = undefined
    this.setOpen(false)
    this.pending.forEach(pending => {
      clearTimeout(pending.timer)
      pending.reject(new Error('连接已断开，返回会话后可查看是否受理'))
    })
    this.pending.clear()
  }
}

export async function readImage(file: File): Promise<{ base64: string; type: string; url: string }> {
  if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type)) {
    throw new Error('请选择 PNG、JPEG、WebP 或 GIF 图片')
  }

  if (file.size > 6 * 1024 * 1024) {
    throw new Error('图片超过 6 MB，请缩小后上传')
  }

  const url = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () =>
      typeof reader.result === 'string' ? resolve(reader.result) : reject(new Error('图片无法读取'))
    reader.onerror = () => reject(new Error('图片无法读取'))
    reader.readAsDataURL(file)
  })

  return { base64: url.slice(url.indexOf(',') + 1), type: file.type, url }
}
