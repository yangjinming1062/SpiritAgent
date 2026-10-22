import type { Net } from 'electron'

import { errorMessage, sleep } from '@runtime'

import { type ApiArguments, type ApiOperationKey, type ApiResponse } from '../../../shared/api'
import { resolveNormalizedBackendUrl } from '../shared/config'

import { createBackendClient } from './client'

interface BackendHttpOptions {
  electronNet: Net
  spiritagentHome: null | string
}

export function createElectronFetch(electronNet: Pick<Net, 'fetch'>): typeof globalThis.fetch {
  return (input, init) => electronNet.fetch(input instanceof URL ? input.href : input, init)
}

export function createBackendHttp({ electronNet, spiritagentHome }: BackendHttpOptions) {
  const fetchImpl = createElectronFetch(electronNet)

  function invoke<K extends ApiOperationKey>(
    baseUrl: string,
    token: string | undefined,
    operation: K,
    ...args: ApiArguments<K>
  ): Promise<ApiResponse<K>> {
    return createBackendClient({ baseUrl, fetch: fetchImpl, token }).invoke(operation, ...args)
  }

  async function mintWsTicket(baseUrl: string, token: string, streamId?: string): Promise<string> {
    const response = await invoke(
      baseUrl,
      token,
      'gateway.gateway_ticket',
      {
        body: streamId ? { stream_id: streamId } : undefined
      },
      { timeoutMs: 5000 }
    )

    if (!response.ticket) {
      throw new Error('Backend returned an empty WebSocket ticket')
    }

    return response.ticket
  }

  async function waitForSpiritAgent(baseUrl: string, token?: string): Promise<void> {
    const deadline = Date.now() + 45_000
    let lastError: unknown = null

    while (Date.now() < deadline) {
      try {
        await invoke(baseUrl, token, 'system.health', {}, { timeoutMs: Math.min(5000, deadline - Date.now()) })

        return
      } catch (error) {
        lastError = error
        await sleep(Math.min(500, Math.max(0, deadline - Date.now())))
      }
    }

    throw new Error(`SpiritAgent backend did not become ready: ${errorMessage(lastError) || 'timeout'}`)
  }

  async function resolveRemoteBackend(): Promise<null | string> {
    return resolveNormalizedBackendUrl(spiritagentHome)
  }

  return { invoke, mintWsTicket, resolveRemoteBackend, waitForSpiritAgent }
}

export type BackendHttp = ReturnType<typeof createBackendHttp>
