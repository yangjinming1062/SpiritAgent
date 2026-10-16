import { isAiWaitingApi } from '@protocol'

import { ApiError, readHttpError } from '../shared/errors'
import {
  type GatewayEvent,
  JsonRpcGatewayClient,
  type RpcMethod,
  type RpcRequest,
  type RpcResponse
} from '../shared/gateway'
export { ApiError } from '../shared/errors'
export type { GatewayEvent } from '../shared/gateway'

import type { RemoteSession } from './types'

export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
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

  async request<T>(
    path: string,
    method = 'GET',
    body?: unknown,
    timeoutMs = isAiWaitingApi(path, method) ? 0 : 180_000
  ): Promise<T> {
    const revision = this.revision
    const controller = new AbortController()
    this.controllers.add(controller)
    const timer = timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : undefined
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

        const business = readHttpError(value) ?? {
          code: 'http_error',
          message: `请求失败（${response.status}）`,
          details: null,
          request_id: ''
        }
        throw new ApiError(response.status, business)
      }

      return value as T
    } finally {
      clearTimeout(timer)
      this.controllers.delete(controller)
    }
  }
}

export class RemoteGateway {
  private readonly gateway = new JsonRpcGatewayClient(
    url => new WebSocket(url),
    error => console.error('Gateway event consumer failed', error)
  )
  private stopped = false
  private connecting: Promise<void> | null = null
  private retryTimer: ReturnType<typeof setTimeout> | undefined

  constructor(
    private api: RemoteApi,
    private invalidated: () => void
  ) {
    this.gateway.onState(state => {
      if (!this.stopped && (state === 'closed' || state === 'error')) {
        if ([1008, 4401, 4403].includes(this.gateway.lastCloseCode ?? 0)) {
          this.invalidated()
        } else {
          this.reconnect()
        }
      }
    })
  }

  get open(): boolean {
    return this.gateway.connectionState === 'open'
  }

  subscribeEvent(handler: (event: GatewayEvent) => void): () => void {
    return this.gateway.onEvent(handler)
  }

  subscribeState(handler: (open: boolean) => void): () => void {
    return this.gateway.onState(state => handler(state === 'open'))
  }

  connect(): Promise<void> {
    if (this.stopped || this.open) {
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
    try {
      const { ticket } = await this.api.request<{ ticket: string }>('/api/gateway/ticket', 'POST', {})
      if (this.stopped) {
        return
      }
      const url = new URL('/api/gateway/ws', location.origin)
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      url.searchParams.set('ticket', ticket)
      await this.gateway.connect(url.href)
    } catch {
      this.reconnect()
    }
  }

  close(): void {
    this.stopped = true
    clearTimeout(this.retryTimer)
    this.gateway.close()
  }

  request<K extends RpcMethod>(method: K, params: RpcRequest<K>): Promise<RpcResponse<K>> {
    return this.gateway.request(method, params)
  }

  private reconnect(): void {
    if (this.stopped) {
      return
    }
    clearTimeout(this.retryTimer)
    this.retryTimer = setTimeout(() => void this.connect(), 2500)
  }
}

export async function readImage(
  file: File
): Promise<{ base64: string; type: 'image/png' | 'image/jpeg' | 'image/webp' | 'image/gif'; url: string }> {
  const type = (['image/png', 'image/jpeg', 'image/webp', 'image/gif'] as const).find(value => value === file.type)
  if (!type) {
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

  return { base64: url.slice(url.indexOf(',') + 1), type, url }
}
