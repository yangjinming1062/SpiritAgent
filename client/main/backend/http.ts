import { sleep } from '@runtime'
import type { App, Net } from 'electron'

import { DEFAULT_FETCH_TIMEOUT_MS, resolveTimeoutMs } from '../security/hardening'
import { resolveNormalizedBackendUrl } from '../shared/config'
import { errorMessage, HttpError } from '../shared/utils'

interface BackendHttpOptions {
  app: Pick<App, 'getVersion'>
  electronNet: Net
  rememberLog: (chunk: string) => void
  spiritagentHome: null | string
}

// 纯网络层：fetchJson / mintWsTicket / waitForSpiritAgent。
// 不依赖启动状态机、窗口状态、动态 token——这些由 entry.ts 的 ensureBackend 编排。
export function createBackendHttp({ app, electronNet, rememberLog, spiritagentHome }: BackendHttpOptions) {
  function resolveSpiritAgentVersion(): string {
    return app.getVersion()
  }

  async function fetchJson(
    url: string,
    token?: string,
    options: { body?: unknown; method?: string; timeoutMs?: number } = {}
  ): Promise<unknown> {
    const timeoutMs = resolveTimeoutMs(options.timeoutMs, DEFAULT_FETCH_TIMEOUT_MS)
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

    const text = await res.text().catch(() => '')

    if (!res.ok) {
      let pathname = url

      try {
        pathname = new URL(url).pathname
      } catch {
        /* ignore invalid url formatting in error */
      }

      throw new HttpError(res.status, `${res.status} ${pathname}: ${text || res.statusText}`)
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

  async function mintWsTicket(baseUrl: string, token: string | null): Promise<string | null> {
    if (!token) {
      return null
    }

    try {
      const res = (await fetchJson(`${baseUrl}/api/user/ws-ticket`, token, {
        method: 'POST',
        timeoutMs: 5000
      })) as { access_token?: string }

      return res?.access_token || null
    } catch (error: unknown) {
      const msg = errorMessage(error)
      rememberLog(`[ws-ticket] mint failed: ${msg}`)

      return null
    }
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

  return { fetchJson, mintWsTicket, resolveRemoteBackend, resolveSpiritAgentVersion, waitForSpiritAgent }
}

export type BackendHttp = ReturnType<typeof createBackendHttp>
