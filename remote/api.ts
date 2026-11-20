import type { ApiArguments, ApiOperationKey, ApiResponse, ApiTransport, HttpCallOptions } from '../shared/api'
import { createHttpClient } from '../shared/api'
import { ApiError } from '../shared/errors'
import { GatewayConnection, type GatewayEvent, type SessionSnapshot } from '../shared/gateway'
import { REALTIME_CALL_VERSION } from '../shared/realtime-call'
import { uiCopy } from '../shared/ui-copy'
export { ApiError } from '../shared/errors'
export type { GatewayEvent } from '../shared/gateway'

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : uiCopy.operationFailed
}

export class RemoteApi implements ApiTransport {
  private readonly controller = new AbortController()
  private readonly transport: ApiTransport

  constructor(
    csrf = '',
    readonly onExpired: () => void = () => {}
  ) {
    this.transport = createHttpClient({
      fetch: (url, init) => fetch(url, init),
      credentials: 'same-origin',
      defaultTimeoutMs: 180_000,
      headers: () => new Headers(csrf ? { 'X-CSRF-Token': csrf } : undefined)
    })
  }

  close(): void {
    this.controller.abort()
  }

  async invoke<K extends ApiOperationKey>(operation: K, ...args: ApiArguments<K>): Promise<ApiResponse<K>> {
    const { signal } = this.controller
    const [input, options] = args
    try {
      const value = await this.transport.invoke<K>(operation, input ?? {}, {
        ...options,
        signal: options?.signal ? AbortSignal.any([options.signal, signal]) : signal
      })
      signal.throwIfAborted()
      return value
    } catch (error) {
      if (signal.aborted) {
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
      if (error instanceof ApiError && error.isLoginExpired) {
        this.onExpired()
      }
      throw error
    }
  }

  async realtimeUrl(sessionId: string, options?: HttpCallOptions): Promise<string> {
    const { ticket } = await this.invoke(
      'gateway.gateway_ticket',
      {
        body: { stream_id: `realtime:${sessionId}` }
      },
      options
    )
    const url = new URL(`/api/realtime_voice/${encodeURIComponent(sessionId)}`, location.origin)
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    url.searchParams.set('ticket', ticket)
    url.searchParams.set('version', String(REALTIME_CALL_VERSION))
    return url.href
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
          body: { stream_id: streamId }
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
