import { app } from 'electron'

import { type ApiTransport, createHttpClient, type HttpFetch } from '../../../shared/api'
import { ApiError } from '../../../shared/errors'

export type FetchFunction = HttpFetch

interface BackendClientOptions {
  baseUrl: string
  fetch: FetchFunction
}

export interface BackendClient extends ApiTransport {
  baseUrl: string
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

export function createBackendClient(options: BackendClientOptions): BackendClient {
  const baseUrl = normalizeBaseUrl(options.baseUrl)

  const transport = createHttpClient({
    baseUrl,
    fetch: options.fetch,
    headers: () => ({ 'User-Agent': `SpiritAgentDesktop/${app.getVersion()} (Electron)` })
  })

  return { baseUrl, invoke: transport.invoke }
}
