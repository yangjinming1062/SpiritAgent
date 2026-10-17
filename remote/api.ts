import type { ApiArguments, ApiOperationKey, ApiResponse, ApiTransport } from '../shared/api'
import { createHttpClient } from '../shared/api'
import { ApiError } from '../shared/errors'
import { GatewayConnection, type GatewayEvent, type SessionSnapshot } from '../shared/gateway'
export { ApiError } from '../shared/errors'
export type { GatewayEvent } from '../shared/gateway'

import type { RemoteSession } from './types'

export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试'
}

export class RemoteApi implements ApiTransport {
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

  async invoke<Operation extends ApiOperationKey>(
    operation: Operation,
    ...args: ApiArguments<Operation>
  ): Promise<ApiResponse<Operation>> {
    const revision = this.revision
    const controller = new AbortController()
    this.controllers.add(controller)
    try {
      const transport = createHttpClient({
        fetch: (input, options) =>
          fetch(input, {
            ...options,
            signal: options?.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal
          }),
        credentials: 'same-origin',
        defaultTimeoutMs: 180_000,
        headers: () => new Headers(this.csrf ? { 'X-CSRF-Token': this.csrf } : undefined)
      })
      const value = await transport.invoke(operation, ...args)
      if (revision !== this.revision) {
        throw new DOMException('账户已切换', 'AbortError')
      }
      return value
    } catch (error) {
      if (revision !== this.revision || controller.signal.aborted) {
        throw new ApiError(
          null,
          {
            code: 'cancelled',
            message: '账户已切换，请求已取消',
            details: null,
            request_id: ''
          },
          { cause: error }
        )
      }
      if (revision === this.revision && error instanceof ApiError && error.isLoginExpired && this.csrf) {
        this.onExpired()
      }
      throw error
    } finally {
      this.controllers.delete(controller)
    }
  }
}

export class RemoteGateway {
  private readonly connection: GatewayConnection
  private stopped = false
  private connectionError = ''
  private readonly stateHandlers = new Set<(open: boolean, error: string) => void>()

  constructor(private api: RemoteApi) {
    this.connection = new GatewayConnection({
      createSocket: url => new WebSocket(url),
      url: async streamId => {
        const { ticket } = await this.api.invoke('gateway.gateway_ticket', {
          body: { stream_id: streamId ?? null }
        })
        const url = new URL('/api/gateway/ws', location.origin)
        url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
        url.searchParams.set('ticket', ticket)
        return url.href
      },
      onExpired: () => {
        if (!this.stopped) {
          this.api.onExpired()
        }
      },
      onError: error => {
        if (this.stopped) {
          return
        }
        this.connectionError = errorText(error)
        this.notifyState()
        if (error instanceof ApiError && error.code === 'gateway_policy_rejected') {
          void this.api.invoke('remote.current_remote_session').catch(failure => {
            if (!this.stopped) {
              this.connectionError = errorText(failure)
              this.notifyState()
            }
          })
        }
      }
    })
    this.connection.client.onState(state => {
      if (state === 'connecting' || state === 'open') {
        this.connectionError = ''
      }
      this.notifyState()
    })
  }

  get open(): boolean {
    return this.connection.client.connectionState === 'open'
  }

  get error(): string {
    return this.connectionError
  }

  get lastRecovery() {
    return this.connection.client.lastRecovery
  }

  subscribeEvent(handler: (event: GatewayEvent) => void): () => void {
    return this.connection.client.onEvent(handler)
  }

  subscribeState(handler: (open: boolean, error: string) => void): () => void {
    this.stateHandlers.add(handler)
    handler(this.open, this.error)
    return () => this.stateHandlers.delete(handler)
  }

  private notifyState(): void {
    this.stateHandlers.forEach(handler => handler(this.open, this.error))
  }

  snapshot(sessionId: string): Promise<SessionSnapshot> {
    return this.connection.client.synchronize(sessionId, streamId =>
      this.api.invoke('sessions.snapshot', {
        path: { session_id: sessionId },
        ...(streamId ? { query: { stream_id: streamId } } : {})
      })
    )
  }

  connect(): Promise<void> {
    return this.connection.connect()
  }

  close(): void {
    this.stopped = true
    this.connection.close()
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
