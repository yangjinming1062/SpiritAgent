import { sleep } from '@runtime'
import type { App, Net } from 'electron'

import { DEFAULT_FETCH_TIMEOUT_MS, resolveTimeoutMs } from '../security/hardening'
import { resolveNormalizedBackendUrl } from '../shared/config'
import { errorMessage, HttpError } from '../shared/utils'

interface BackendHttpOptions {
  app: Pick<App, 'getVersion'>
  electronNet: Net
  spiritagentHome: null | string
}

// net.fetch 走 Chromium 网络栈（系统代理与证书）；其签名不收 URL 对象，统一在此适配为标准 fetch。
export function createElectronFetch(electronNet: Pick<Net, 'fetch'>): typeof globalThis.fetch {
  return (input, init) => electronNet.fetch(input instanceof URL ? input.href : input, init)
}

export function createBackendHttp({ app, electronNet, spiritagentHome }: BackendHttpOptions) {
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

    if (!res.ok) {
      // 错误响应体只用于诊断文案，读取失败时回落状态文本。
      const detail = await res.text().catch(() => '')
      let pathname = url

      try {
        pathname = new URL(url).pathname
      } catch {
        /* ignore invalid url formatting in error */
      }

      throw new HttpError(res.status, `${res.status} ${pathname}: ${detail || res.statusText}`)
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

  return { fetchJson, mintWsTicket, resolveRemoteBackend, resolveSpiritAgentVersion, waitForSpiritAgent }
}

export type BackendHttp = ReturnType<typeof createBackendHttp>
