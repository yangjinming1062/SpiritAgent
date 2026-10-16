import { net } from 'electron'

import type {
  DesktopGatewaySnapshot,
  DesktopGatewayState,
  MemoryToolScope,
  RunnerCallOutcome,
  RunnerCallRequest
} from '@ipc/contracts'
import { errorMessage } from '@runtime'

import { type ApiSchemas, RPC_POLICIES } from '../../../shared/api.generated'
import {
  type GatewayEvent,
  isJsonValue,
  isRecord,
  JsonRpcGatewayClient,
  type RpcMethod,
  type RpcRequest
} from '../../../shared/gateway'
import type { RunnerTools } from '../../../shared/runner'
import type { BackendConnection } from '../shared/backend-port'
import { isUnauthorized } from '../shared/utils'

interface GatewayOptions {
  ensureBackend: () => Promise<BackendConnection>
  getCurrentAuth: () => null | { sessionId: string; token: string }
  getTools: () => RunnerTools
  dispatchCall: (request: RunnerCallRequest) => Promise<RunnerCallOutcome>
  cancelCall: (callId: string) => Promise<unknown>
  mintWsTicket: (baseUrl: string, token: string) => Promise<string>
  syncTimezone: (baseUrl: string, token: string) => Promise<void>
  onEvent: (event: GatewayEvent, authSessionId: string) => void
  onState: (snapshot: DesktopGatewaySnapshot, authSessionId: string) => void
  onExpired: (authSessionId: string) => void
  log: (message: string) => void
}

function callResult(outcome: RunnerCallOutcome): ApiSchemas['JsonValue'] {
  switch (outcome.status) {
    case 'completed':
      return isJsonValue(outcome.result)
        ? outcome.result
        : { ok: false, error: 'Runner returned an invalid JSON result; the outcome is unknown.' }

    case 'failed':
      return { ok: false, error: outcome.error }

    case 'not_executed':
      return { ok: false, error: 'Not executed: the call did not reach the local runner.' }

    case 'unknown':
      return {
        ok: false,
        error:
          'The local runner call ended without a result, so the outcome is unknown: the tool may or may not have run. Do not rerun it automatically; check its effects or ask the user first.'
      }
  }
}

/** 账户连接归主进程所有；窗口重载和视图切换不改变网络与设备调用生命周期。 */
export function createDesktopGateway(options: GatewayOptions) {
  let current: { sessionId: string; token: string } | null = null
  let client: JsonRpcGatewayClient | null = null
  let revision = 0
  let connecting: Promise<void> | null = null
  let retry: ReturnType<typeof setTimeout> | undefined
  let attempt = 0
  let expired = false
  let snapshot: DesktopGatewaySnapshot = { state: 'idle', error: null, hasConnected: false, activeToolCount: 0 }
  let connectionToken: string | null = null
  let toolsGeneration = 0
  let toolsSignature: string | null = null
  let toolsTail = Promise.resolve()
  const active = new Map<string, { cancelled: boolean; headless: boolean }>()
  const seen = new Set<string>()

  const isCurrent = (connection: JsonRpcGatewayClient, generation: number): boolean =>
    client === connection && generation === revision && current?.sessionId === options.getCurrentAuth()?.sessionId

  function publishState(state: DesktopGatewayState, error = snapshot.error): void {
    if (!current) {
      return
    }

    const next = {
      state,
      error: state === 'open' ? null : error,
      hasConnected: snapshot.hasConnected || state === 'open',
      activeToolCount: [...active.values()].filter(call => !call.cancelled && !call.headless).length
    }

    if (
      next.state !== snapshot.state ||
      next.error !== snapshot.error ||
      next.hasConnected !== snapshot.hasConnected ||
      next.activeToolCount !== snapshot.activeToolCount
    ) {
      snapshot = next
      options.onState(snapshot, current.sessionId)
    }
  }

  function scheduleReconnect(): void {
    if (!current || expired || retry) {
      return
    }

    retry = setTimeout(
      () => {
        retry = undefined
        void connect()
      },
      Math.min(1000 * 2 ** Math.min(attempt++, 5), 30_000)
    )
  }

  async function syncTools(readyTools?: RunnerTools): Promise<void> {
    const connection = client
    const epoch = revision
    const generation = ++toolsGeneration

    if (!connection || connection.connectionState !== 'open') {
      return
    }

    const tools = readyTools ?? options.getTools()
    const signature = JSON.stringify(tools)

    const task = toolsTail.then(async () => {
      if (
        !isCurrent(connection, epoch) ||
        generation !== toolsGeneration ||
        signature === toolsSignature ||
        connection.connectionState !== 'open'
      ) {
        return
      }

      await connection.request('tools.sync', { tools, skill_scope_version: 1 })

      if (isCurrent(connection, epoch) && connection.connectionState === 'open') {
        toolsSignature = signature
      }
    })

    toolsTail = task.catch(error => options.log(`[gateway] tools.sync failed: ${errorMessage(error)}`))
    await toolsTail
  }

  function executeDeviceCommand(event: GatewayEvent, connection: JsonRpcGatewayClient, epoch: number): boolean {
    if (event.type !== 'tool.call' && event.type !== 'tool.cancel') {
      return false
    }

    const payload = isRecord(event.payload) ? event.payload : {}
    const callId = payload.call_id

    if (typeof callId !== 'string' || !callId) {
      return true
    }

    if (event.type === 'tool.cancel') {
      const call = active.get(callId)

      if (call && !call.cancelled) {
        call.cancelled = true
        publishState(snapshot.state)
        void options.cancelCall(callId).catch(error => options.log(`[gateway] cancel failed: ${errorMessage(error)}`))
      }

      return true
    }

    if (seen.has(callId)) {
      return true
    }

    seen.add(callId)

    if (seen.size > 500) {
      seen.delete(seen.values().next().value!)
    }

    const scope = payload.skill_scope
    let skillScope: MemoryToolScope | undefined

    if (scope != null) {
      if (
        !isRecord(scope) ||
        !Number.isInteger(scope.user_id) ||
        typeof scope.user_id !== 'number' ||
        scope.user_id <= 0 ||
        (scope.system_preset_id !== 'companion' &&
          scope.system_preset_id !== 'copywriter' &&
          scope.system_preset_id !== 'language_teacher')
      ) {
        void connection
          .request('tool.result', {
            call_id: callId,
            result: { ok: false, error: 'Invalid server-controlled skill scope' }
          })
          .catch(error => options.log(`[gateway] rejected tool result delivery failed: ${errorMessage(error)}`))

        return true
      }

      skillScope = { user_id: scope.user_id, system_preset_id: scope.system_preset_id }
    }

    const call = { cancelled: false, headless: payload.headless === true }
    active.set(callId, call)
    publishState(snapshot.state)

    void (async () => {
      try {
        const outcome = await options.dispatchCall({
          callId,
          name: typeof payload.name === 'string' ? payload.name : '',
          args: isRecord(payload.args) ? payload.args : {},
          ...(skillScope ? { skillScope } : {})
        })

        if (!call.cancelled && isCurrent(connection, epoch)) {
          await connection.request('tool.result', { call_id: callId, result: callResult(outcome) })
        }
      } catch (error) {
        options.log(`[gateway] tool result delivery failed: ${errorMessage(error)}`)
      } finally {
        if (active.get(callId) === call) {
          active.delete(callId)
          publishState(snapshot.state)
        }
      }
    })()

    return true
  }

  function replaceAuth(): void {
    const next = options.getCurrentAuth()

    if (next?.sessionId === current?.sessionId) {
      const credentialChanged = next?.token !== current?.token
      current = next

      if (credentialChanged) {
        expired = false
        clearTimeout(retry)
        retry = undefined

        if (client?.connectionState === 'open') {
          connectionToken = next?.token ?? null
        }

        const pending = connecting

        if (pending) {
          void pending.finally(() => {
            if (connecting === pending) {
              connecting = null
            }

            void connect()
          })
        }
      }

      return
    }

    revision++
    toolsGeneration++
    toolsSignature = null
    clearTimeout(retry)
    retry = undefined
    const previous = client
    client = null
    previous?.close()
    current = next
    connecting = null
    snapshot = { state: 'idle', error: null, hasConnected: false, activeToolCount: 0 }
    connectionToken = null

    for (const [callId, call] of active) {
      call.cancelled = true
      void options
        .cancelCall(callId)
        .catch(error => options.log(`[gateway] account cancellation failed: ${errorMessage(error)}`))
    }

    active.clear()
    seen.clear()
    attempt = 0
    expired = false

    if (!next) {
      return
    }

    const epoch = revision

    const connection = new JsonRpcGatewayClient(
      url => new net.WebSocket(url),
      error => options.log(`[gateway] event consumer failed: ${errorMessage(error)}`)
    )

    client = connection
    connection.onEvent(event => {
      if (isCurrent(connection, epoch) && !executeDeviceCommand(event, connection, epoch)) {
        options.onEvent(event, next.sessionId)
      }
    })
    connection.onState(state => {
      if (!isCurrent(connection, epoch)) {
        return
      }

      // 已建立连接仍须处理断开；仅忽略同一登录刷新凭据后的旧握手失败。
      if (
        (state === 'closed' || state === 'error') &&
        snapshot.state !== 'open' &&
        connectionToken !== options.getCurrentAuth()?.token
      ) {
        return
      }

      publishState(state, snapshot.error ?? (state === 'error' ? 'Could not connect to SpiritAgent gateway' : null))

      if (state === 'open') {
        connectionToken = options.getCurrentAuth()?.token ?? null
        clearTimeout(retry)
        retry = undefined
        attempt = 0
        toolsSignature = null
        void syncTools()
      } else if (state === 'closed' || state === 'error') {
        toolsGeneration++
        toolsSignature = null

        if ([1008, 4401, 4403].includes(connection.lastCloseCode ?? 0)) {
          expired = true
          options.onExpired(next.sessionId)
        } else {
          scheduleReconnect()
        }
      }
    })
  }

  async function connect(): Promise<void> {
    replaceAuth()

    if (!current || expired || !client || client.connectionState === 'open') {
      return
    }

    if (connecting) {
      return connecting
    }

    const connection = client
    const epoch = revision
    const auth = current
    connectionToken = auth.token
    publishState('connecting')

    const task = (async () => {
      try {
        const backend = await options.ensureBackend()

        if (
          !isCurrent(connection, epoch) ||
          backend.token !== auth.token ||
          options.getCurrentAuth()?.token !== auth.token
        ) {
          return
        }

        void options
          .syncTimezone(backend.baseUrl, auth.token)
          .catch(error => options.log(`[gateway] timezone update failed: ${errorMessage(error)}`))
        const ticket = await options.mintWsTicket(backend.baseUrl, auth.token)

        if (!isCurrent(connection, epoch) || options.getCurrentAuth()?.token !== auth.token) {
          return
        }

        await connection.connect(
          `${backend.baseUrl.replace(/^http/, 'ws')}/api/gateway/ws?ticket=${encodeURIComponent(ticket)}`
        )
      } catch (error) {
        if (isCurrent(connection, epoch) && options.getCurrentAuth()?.token === auth.token) {
          const message = errorMessage(error) || 'Could not connect to SpiritAgent gateway'
          publishState('error', message)
          options.log(`[gateway] connection failed: ${message}`)

          if (isUnauthorized(error)) {
            expired = true
            options.onExpired(auth.sessionId)
          } else {
            scheduleReconnect()
          }
        }
      }
    })()

    connecting = task
    await task

    if (connecting === task) {
      connecting = null
    }

    // 定时刷新凭据不一定广播账户变化；旧准备任务结束后接续最新凭据。
    if (isCurrent(connection, epoch) && options.getCurrentAuth()?.token !== auth.token) {
      void connect()
    }
  }

  function authChanged(): void {
    replaceAuth()
    void connect()
  }

  return {
    authChanged,
    reconnect: () => {
      clearTimeout(retry)
      retry = undefined
      attempt = 0
      void connect()
    },
    runnerStatus: (tools: RunnerTools) => void syncTools(tools),
    getState: (authSessionId: string): DesktopGatewaySnapshot => {
      replaceAuth()

      if (!current || current.sessionId !== authSessionId) {
        throw new Error('Gateway authentication changed')
      }

      void connect()

      return snapshot
    },
    request: async (authSessionId: string, method: RpcMethod, params: RpcRequest<RpcMethod>): Promise<unknown> => {
      replaceAuth()
      const connection = client
      const epoch = revision

      if (!current || current.sessionId !== authSessionId || !connection) {
        throw new Error('Gateway authentication changed')
      }

      if (!Object.hasOwn(RPC_POLICIES, method)) {
        throw new Error('Unknown gateway method')
      }

      if (['tools.sync', 'tool.result'].includes(method)) {
        throw new Error('Gateway device operations are restricted to the main process')
      }

      const result = await connection.request(method, params)

      if (!isCurrent(connection, epoch)) {
        throw new Error('Gateway authentication changed')
      }

      return result
    },
    close: (): void => {
      revision++
      current = null
      clearTimeout(retry)
      retry = undefined
      client?.close()
      client = null
    }
  }
}

export type DesktopGateway = ReturnType<typeof createDesktopGateway>
