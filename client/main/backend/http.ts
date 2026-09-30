import { sleep } from '@runtime'
import type { Net } from 'electron'

import { resolveTimeoutMs } from '../security/hardening'
import { resolveNormalizedBackendUrl } from '../shared/config'
import { errorMessage, httpErrorFromResponse } from '../shared/utils'

interface BackendHttpOptions {
  electronNet: Net
  spiritagentHome: null | string
}

// net.fetch 走 Chromium 网络栈（系统代理与证书）；其签名不收 URL 对象，统一在此适配为标准 fetch。
export function createElectronFetch(electronNet: Pick<Net, 'fetch'>): typeof globalThis.fetch {
  return (input, init) => electronNet.fetch(input instanceof URL ? input.href : input, init)
}

export function createBackendHttp({ electronNet, spiritagentHome }: BackendHttpOptions) {
  async function fetchJson(
    url: string,
    token?: string,
    options: { body?: unknown; method?: string; timeoutMs?: number } = {}
  ): Promise<unknown> {
    const timeoutMs = resolveTimeoutMs(options.timeoutMs)
    const body = options.body !== undefined ? JSON.stringify(options.body) : undefined

    const headers: Record<string, string> = {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    }

    const res = await electronNet.fetch(url, {
      body,
      headers,
      method: options.method || 'GET',
      signal: AbortSignal.timeout(timeoutMs)
    })

    if (!res.ok) {
      throw await httpErrorFromResponse(res, new URL(url).pathname)
    }

    let text: string

    try {
      text = await res.text()
    } catch (error) {
      throw new Error(`Failed to read response from ${url} (status ${res.status}): ${errorMessage(error)}`, {
        cause: error
      })
    }

    if (!text) {
      return null
    }

    const looksHtml = /^\s*<(?:!doctype|html)/i.test(text)
    const contentType = String(res.headers.get('content-type') || '')

    if (looksHtml || contentType.includes('text/html')) {
      throw new Error(
        `Expected JSON from ${url} but got HTML (status ${res.status}). The endpoint is likely missing on the SpiritAgent backend.`
      )
    }

    try {
      return JSON.parse(text)
    } catch {
      throw new Error(`Invalid JSON from ${url} (status ${res.status}): ${text.slice(0, 200)}`)
    }
  }

  async function mintWsTicket(baseUrl: string, token: string): Promise<string> {
    const res = (await fetchJson(`${baseUrl}/api/user/ws-ticket`, token, {
      method: 'POST',
      timeoutMs: 5000
    })) as { access_token?: unknown } | null

    if (typeof res?.access_token !== 'string' || !res.access_token) {
      throw new Error('Backend returned an empty WebSocket ticket')
    }

    return res.access_token
  }

  async function waitForSpiritAgent(baseUrl: string, token?: string): Promise<void> {
    const deadline = Date.now() + 45_000
    let lastError: unknown = null

    while (Date.now() < deadline) {
      try {
        await fetchJson(`${baseUrl}/health`, token)

        return
      } catch (error) {
        lastError = error
        await sleep(500)
      }
    }

    const lastErrorMsg = errorMessage(lastError)

    throw new Error(`SpiritAgent backend did not become ready: ${lastErrorMsg || 'timeout'}`)
  }

  async function resolveRemoteBackend(): Promise<null | { baseUrl: string }> {
    const url = resolveNormalizedBackendUrl(spiritagentHome)

    return url ? { baseUrl: url } : null
  }

  return { fetchJson, mintWsTicket, resolveRemoteBackend, waitForSpiritAgent }
}

export type BackendHttp = ReturnType<typeof createBackendHttp>
