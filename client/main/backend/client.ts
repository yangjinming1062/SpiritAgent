import { app } from 'electron'

import { createHttpClient, type HttpFetch } from '../../../shared/api'
import { ApiError } from '../../../shared/errors'
import type { BackendClientPort } from '../shared/backend-port'

export type FetchFunction = HttpFetch

interface BackendClientOptions {
  baseUrl: string
  fetch: FetchFunction
  /** 登录后的账户令牌；给出时随请求带上 Authorization。 */
  token?: string
}

export function isRetryableBackendError(error: unknown): boolean {
  return error instanceof ApiError && (error.isNetwork || error.isServerError)
}

export function normalizeBaseUrl(raw?: null | string): string {
  let parsed: URL

  try {
    parsed = new URL(String(raw || '').trim())
  } catch (cause) {
    throw new ApiError(
      null,
      {
        code: 'invalid_base_url',
        message: '服务器地址无效',
        details: null,
        request_id: ''
      },
      { cause }
    )
  }

  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw new ApiError(null, {
      code: 'invalid_base_url',
      message: '服务器地址必须使用 HTTP 或 HTTPS',
      details: null,
      request_id: ''
    })
  }

  parsed.hash = ''
  parsed.search = ''

  return parsed.toString().replace(/\/+$/, '')
}

/** 出网请求的唯一入口：地址统一归一，默认头带上客户端标识，登录后再附带账户令牌。 */
export function createBackendClient(options: BackendClientOptions): BackendClientPort {
  const baseUrl = normalizeBaseUrl(options.baseUrl)
  const token = options.token

  const transport = createHttpClient({
    baseUrl,
    fetch: options.fetch,
    headers: () => ({
      'User-Agent': `SpiritAgentDesktop/${app.getVersion()} (Electron)`,
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    })
  })

  return { baseUrl, invoke: transport.invoke }
}
