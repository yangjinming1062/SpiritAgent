import { net } from 'electron'

import type {
  DesktopGatewaySnapshot,
  DesktopGatewayState,
  MemoryToolScope,
  RunnerCallOutcome,
  RunnerCallRequest
} from '@ipc/contracts'
import { errorMessage } from '@runtime'

import { SYSTEM_PRESET_IDS } from '../../../shared/api.generated'
import { ApiError } from '../../../shared/errors'
import {
  GatewayConnection,
  type GatewayEvent,
  isJsonValue,
  type JsonValue,
  type RpcRequest,
  type SessionSnapshot
} from '../../../shared/gateway'
import type { RunnerResponse, RunnerTools } from '../../../shared/runner'
import type { BackendConnection } from '../shared/backend-port'

interface GatewayOptions {
  ensureBackend: () => Promise<BackendConnection>
  getCurrentAuth: () => null | { sessionId: string; token: string }
  getTools: () => RunnerTools
  dispatchCall: (request: RunnerCallRequest) => Promise<RunnerCallOutcome>
  queryCall: (callId: string) => Promise<RunnerResponse<'calls.get'>>
  cancelCall: (callId: string) => Promise<unknown>
  mintWsTicket: (baseUrl: string, token: string, streamId?: string) => Promise<string>
  syncTimezone: (baseUrl: string, token: string) => Promise<void>
  onEvent: (event: GatewayEvent, authSessionId: string) => void
  onState: (snapshot: DesktopGatewaySnapshot, authSessionId: string) => void
  onExpired: (authSessionId: string) => void
  log: (message: string) => void
}

/** 技能作用域只接受预设目录内的 id；清单由后端目录生成，目录与 Runner 契约的漏同步在生成阶段失败。 */
function isSystemPresetId(value: string): value is MemoryToolScope['system_preset_id'] {
  return (SYSTEM_PRESET_IDS as readonly string[]).includes(value)
}

function callResult(outcome: RunnerCallOutcome): JsonValue {
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
        error: 'The tool outcome is unknown. It may have run; check its effects before considering another execution.'
      }
  }
}

function emptySnapshot(): DesktopGatewaySnapshot {
  return { state: 'idle', error: null, hasConnected: false, activeToolCount: 0, recovery: null, streamId: null }
}

export function createDesktopGateway(options: GatewayOptions) {
  let authSessionId: string | null = null
  let revision = 0
  let connection: GatewayConnection | null = null
  let snapshot = emptySnapshot()
  let toolsSignature: string | null = null
  let toolsGeneration = 0
  let toolsTail = Promise.resolve()
  const active = new Map<string, { cancelled: boolean; headless: boolean }>()
  const receipts = new Map<string, JsonValue>()
  const delivering = new Set<string>()
  const completed = new Set<string>()
  const cancelled = new Set<string>()
  const recovering = new Set<string>()
  const recoveryTimers = new Map<string, ReturnType<typeof setTimeout>>()
  const receiptTimers = new Map<string, ReturnType<typeof setTimeout>>()

  const isCurrent = (epoch: number): boolean =>
    epoch === revision && authSessionId === options.getCurrentAuth()?.sessionId

  function publish(state: DesktopGatewayState, error = snapshot.error): void {
    if (!authSessionId) {
      return
    }

    const next: DesktopGatewaySnapshot = {
      state,
      error: state === 'open' ? null : error,
      hasConnected: snapshot.hasConnected || state === 'open',
      activeToolCount: [...active.values()].filter(call => !call.cancelled && !call.headless).length,
      recovery: connection?.client.lastRecovery?.recovery ?? null,
      streamId: connection?.client.streamId || null
    }

    if (JSON.stringify(next) !== JSON.stringify(snapshot)) {
      snapshot = next
      options.onState(snapshot, authSessionId)
    }
  }

  async function deliver(callId: string, epoch: number): Promise<void> {
    const client = connection?.client

    if (!isCurrent(epoch) || !receipts.has(callId) || delivering.has(callId) || client?.connectionState !== 'open') {
      return
    }

    delivering.add(callId)

    try {
      await client.request('tool.result', { call_id: callId, result: receipts.get(callId)! })

      if (isCurrent(epoch)) {
        clearTimeout(receiptTimers.get(callId))
        receiptTimers.delete(callId)
        receipts.delete(callId)
        completed.add(callId)

        if (completed.size > 500) {
          completed.delete(completed.values().next().value!)
        }
      }
    } catch (error) {
      if (isCurrent(epoch)) {
        options.log('[gateway] tool receipt retained: ' + errorMessage(error))

        if (!receiptTimers.has(callId)) {
          receiptTimers.set(
            callId,
            setTimeout(() => {
              receiptTimers.delete(callId)
              void deliver(callId, epoch)
            }, 5000)
          )
        }
      }
    } finally {
      if (isCurrent(epoch)) {
        delivering.delete(callId)
      }
    }
  }

  async function recoverCall(callId: string, epoch: number): Promise<void> {
    if (
      !isCurrent(epoch) ||
      connection?.client.connectionState !== 'open' ||
      active.has(callId) ||
      receipts.has(callId) ||
      completed.has(callId) ||
      recovering.has(callId) ||
      cancelled.has(callId)
    ) {
      return
    }

    recovering.add(callId)

    try {
      const recorded = await options.queryCall(callId)

      if (!isCurrent(epoch)) {
        return
      }

      if (recorded.status === 'claimed') {
        const client = connection?.client

        if (client?.connectionState !== 'open') {
          return
        }

        const status = await client.request('gateway.resume', { stream_id: client.streamId, last_seq: client.sequence })

        if (!isCurrent(epoch) || cancelled.has(callId) || !status.pending_calls.includes(callId)) {
          return
        }

        const timer = setTimeout(() => {
          recoveryTimers.delete(callId)
          void recoverCall(callId, epoch)
        }, 5000)

        recoveryTimers.set(callId, timer)

        return
      }

      receipts.set(
        callId,
        recorded.status === 'completed' && isJsonValue(recorded.result)
          ? recorded.result
          : {
              ok: false,
              error:
                recorded.error ??
                'The original tool result is unavailable. Its effects are unknown; do not rerun it automatically.'
            }
      )
      await deliver(callId, epoch)
    } catch (error) {
      if (isCurrent(epoch)) {
        options.log('[gateway] tool journal query failed: ' + errorMessage(error))

        if (connection?.client.connectionState === 'open' && !recoveryTimers.has(callId)) {
          recoveryTimers.set(
            callId,
            setTimeout(() => {
              recoveryTimers.delete(callId)
              void recoverCall(callId, epoch)
            }, 5000)
          )
        }
      }
    } finally {
      if (isCurrent(epoch)) {
        recovering.delete(callId)
      }
    }
  }

  function reconcile(): void {
    const epoch = revision

    for (const callId of receipts.keys()) {
      void deliver(callId, epoch)
    }

    for (const callId of connection?.client.lastRecovery?.pending_calls ?? []) {
      void recoverCall(callId, epoch)
    }
  }

  async function syncTools(tools = options.getTools()): Promise<void> {
    const client = connection?.client
    const epoch = revision
    const generation = ++toolsGeneration
    const signature = JSON.stringify(tools)

    const task = toolsTail.then(async () => {
      if (
        !isCurrent(epoch) ||
        generation !== toolsGeneration ||
        signature === toolsSignature ||
        client?.connectionState !== 'open'
      ) {
        return
      }

      await client.request('tools.sync', { tools, skill_scope_version: 1 })

      if (isCurrent(epoch)) {
        toolsSignature = signature
      }
    })

    toolsTail = task.catch(error => options.log('[gateway] tools.sync failed: ' + errorMessage(error)))
    await toolsTail
  }

  function receive(event: GatewayEvent, epoch: number): void {
    if (!isCurrent(epoch)) {
      return
    }

    if (event.type === 'tool.cancel') {
      const callId = event.payload.call_id
      cancelled.add(callId)
      clearTimeout(recoveryTimers.get(callId))
      recoveryTimers.delete(callId)
      const call = active.get(callId)

      if (call) {
        call.cancelled = true
        publish(snapshot.state)
      }

      void options
        .cancelCall(callId)
        .catch(error => options.log('[gateway] cancellation failed: ' + errorMessage(error)))

      return
    }

    if (event.type !== 'tool.call') {
      options.onEvent(event, authSessionId!)

      return
    }

    const payload = event.payload
    const callId = payload.call_id

    if (receipts.has(callId)) {
      void deliver(callId, epoch)

      return
    }

    if (active.has(callId) || completed.has(callId)) {
      return
    }

    if (cancelled.has(callId)) {
      receipts.set(callId, { ok: false, error: 'The call was cancelled before dispatch.' })
      void deliver(callId, epoch)

      return
    }

    let skillScope: MemoryToolScope | undefined

    if (payload.skill_scope) {
      const scope = payload.skill_scope

      if (scope.user_id <= 0 || !Number.isSafeInteger(scope.user_id) || !isSystemPresetId(scope.system_preset_id)) {
        receipts.set(callId, { ok: false, error: 'Invalid server-controlled skill scope' })
        void deliver(callId, epoch)

        return
      }

      skillScope = { user_id: scope.user_id, system_preset_id: scope.system_preset_id }
    }

    const call = { cancelled: false, headless: payload.headless }
    active.set(callId, call)
    publish(snapshot.state)

    void (async () => {
      try {
        const outcome = await options.dispatchCall({
          callId,
          name: payload.name,
          args: payload.args,
          ...(skillScope ? { skillScope } : {})
        })

        if (isCurrent(epoch)) {
          receipts.set(callId, callResult(outcome))
          await deliver(callId, epoch)
        }
      } catch (error) {
        if (isCurrent(epoch)) {
          receipts.set(callId, { ok: false, error: 'The tool result is unknown: ' + errorMessage(error) })
          await deliver(callId, epoch)
        }
      } finally {
        if (isCurrent(epoch) && active.get(callId) === call) {
          active.delete(callId)
          publish(snapshot.state)
        }
      }
    })()
  }

  async function verifyLogin(epoch: number): Promise<void> {
    const current = options.getCurrentAuth()

    if (!current || !isCurrent(epoch)) {
      return
    }

    const token = current.token

    try {
      const backend = await options.ensureBackend()

      if (!isCurrent(epoch) || backend.token !== token || options.getCurrentAuth()?.token !== token) {
        return
      }

      await options.mintWsTicket(backend.baseUrl, token, connection?.client.streamId || undefined)
    } catch (error) {
      if (isCurrent(epoch) && options.getCurrentAuth()?.token === token) {
        if (error instanceof ApiError && error.isLoginExpired) {
          options.onExpired(current.sessionId)
        } else {
          options.log('[gateway] login verification failed: ' + errorMessage(error))
        }
      }
    }
  }

  function replaceAuth(): void {
    const next = options.getCurrentAuth()

    if (next?.sessionId === authSessionId) {
      return
    }

    revision++
    const prior = connection
    connection = null
    prior?.close()

    for (const callId of active.keys()) {
      void options.cancelCall(callId).catch(error => options.log(errorMessage(error)))
    }

    for (const timer of recoveryTimers.values()) {
      clearTimeout(timer)
    }

    for (const timer of receiptTimers.values()) {
      clearTimeout(timer)
    }

    active.clear()
    receipts.clear()
    delivering.clear()
    completed.clear()
    cancelled.clear()
    recovering.clear()
    recoveryTimers.clear()
    receiptTimers.clear()
    authSessionId = next?.sessionId ?? null
    snapshot = emptySnapshot()
    toolsSignature = null
    toolsGeneration++

    if (!next) {
      return
    }

    const epoch = revision

    const transport = new GatewayConnection({
      createSocket: url => new net.WebSocket(url),
      url: async streamId => {
        publish('connecting')
        const backend = await options.ensureBackend()
        const current = options.getCurrentAuth()

        if (!isCurrent(epoch) || !current || current.token !== backend.token) {
          throw new Error('Gateway authentication changed')
        }

        const token = current.token

        const ticket = await options.mintWsTicket(backend.baseUrl, token, streamId).catch(error => {
          if (!isCurrent(epoch) || options.getCurrentAuth()?.token !== token) {
            throw new Error('Gateway authentication changed', { cause: error })
          }

          throw error
        })

        if (!isCurrent(epoch) || options.getCurrentAuth()?.token !== token) {
          throw new Error('Gateway authentication changed')
        }

        void options.syncTimezone(backend.baseUrl, token).catch(error => options.log(errorMessage(error)))

        return backend.baseUrl.replace(/^http/, 'ws') + '/api/gateway/ws?ticket=' + encodeURIComponent(ticket)
      },
      onExpired: () => {
        if (isCurrent(epoch)) {
          options.onExpired(next.sessionId)
        }
      },
      onError: error => {
        if (isCurrent(epoch)) {
          publish('error', errorMessage(error))
          options.log('[gateway] ' + errorMessage(error))

          if (error instanceof ApiError && error.code === 'gateway_policy_rejected') {
            void verifyLogin(epoch)
          }
        }
      }
    })

    connection = transport
    transport.client.onEvent(event => receive(event, epoch))
    transport.client.onState(state => {
      if (!isCurrent(epoch)) {
        return
      }

      publish(state)

      if (state === 'open') {
        toolsSignature = null
        void syncTools()
        reconcile()
      } else if (state === 'closed' || state === 'error') {
        toolsSignature = null
        toolsGeneration++
      }
    })
  }

  function authorize(sessionId: string): GatewayConnection {
    replaceAuth()

    if (!connection || sessionId !== authSessionId) {
      throw new Error('Gateway authentication changed')
    }

    return connection
  }

  return {
    authChanged: () => {
      replaceAuth()
      connection?.reconnect()
    },
    reconnect: () => {
      replaceAuth()
      connection?.reconnect()
    },
    runnerStatus: (tools: RunnerTools) => {
      void syncTools(tools)
      reconcile()
    },
    getState: (sessionId: string): DesktopGatewaySnapshot => {
      const transport = authorize(sessionId)
      void transport.connect()

      return snapshot
    },
    presence: async (sessionId: string, signal: RpcRequest<'companion.signal'>): Promise<void> => {
      await authorize(sessionId).client.request('companion.signal', signal)
    },
    synchronize: (sessionId: string, conversationId: string, load: (streamId?: string) => Promise<SessionSnapshot>) =>
      authorize(sessionId).client.synchronize(conversationId, load),
    close: (): void => {
      revision++
      authSessionId = null
      connection?.close()
      connection = null

      for (const timer of recoveryTimers.values()) {
        clearTimeout(timer)
      }

      for (const timer of receiptTimers.values()) {
        clearTimeout(timer)
      }

      recoveryTimers.clear()
      receiptTimers.clear()
    }
  }
}

export type DesktopGateway = ReturnType<typeof createDesktopGateway>
