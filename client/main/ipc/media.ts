import path from 'node:path'

import type { IpcMain } from 'electron'

import {
  type AttachmentVideoUploadPayload,
  type AttachmentVideoUploadResult,
  type AudioUploadPayload,
  type AudioUploadResult,
  type DesktopVideoUploadPayload,
  type DesktopVideoUploadResult,
  IPC,
  type MediaSttPayload,
  type MediaTtsPayload,
  type VoiceSampleUploadPayload,
  type VoiceSampleUploadResult
} from '@ipc/contracts'
import { errorMessage, sleep } from '@runtime'

import { type ApiTransport, createHttpClient } from '../../../shared/api'
import { speechText } from '../../shared/speech-text'
import { readUserSelectedFile } from '../security/hardening'
import type { BackendConnection } from '../shared/backend-port'
import * as store from '../shared/lib/runner-config-store'
import { dataUrlFromBuffer, mimeTypeForPath, parseDataUrl } from '../shared/mime'

import { registerApiResult } from './api-result'
import { createBackendCaller, type GetCurrentAuth } from './connection'
import { createTtsDiskCache } from './tts-disk-cache'

const TTS_MAX_TEXT_CHARS = 4000
const STT_MAX_AUDIO_BYTES = 24 * 1024 * 1024
const ATTACH_VIDEO_MAX_BYTES = 512 * 1024 * 1024
const ATTACH_VIDEO_TIMEOUT_MS = 120_000
const DESKTOP_VIDEO_UPLOAD_TIMEOUT_MS = 15 * 60_000
const DEFAULT_TTS_LANGUAGE = 'zh'
const DEFAULT_STT_LANGUAGE = 'zh'

/** 媒体调用（STT / TTS）的语言回退：调用方显式 → 当前用户语言 → 兜底默认。 */
function resolveMediaLanguage(payload: { language?: string } | undefined, fallback: string): string {
  if (typeof payload?.language === 'string' && payload.language) {
    return payload.language
  }

  const configLang = store.read().language

  if (typeof configLang === 'string' && configLang) {
    return configLang
  }

  return fallback
}

const MIN_TTS_INTERVAL_MS = 750
const STT_MAX_CONCURRENCY = 2
const STT_BURST = 4
const STT_REFILL_RATE = 2
const TTS_MAX_QUEUE_SIZE = 8
const TTS_CACHE_MAX_ENTRIES = 100
const TTS_CACHE_TTL_MS = 10 * 60 * 1000

let ttsSeq = 0
let sttSeq = 0
const ttsAudioCache: Map<string, { dataUrl: string; expiresAt: number; mimeType: string }> = new Map()
const inflightTts = new Map<string, Promise<{ dataUrl: string; mimeType: string }>>()

class SttLimiter {
  private activeCount = 0
  private lastRefill = Date.now()
  private tokens = STT_BURST

  acquire(): () => void {
    const now = Date.now()
    const elapsed = (now - this.lastRefill) / 1000
    this.tokens = Math.min(STT_BURST, this.tokens + elapsed * STT_REFILL_RATE)
    this.lastRefill = now

    if (this.tokens < 1) {
      throw new Error('STT is busy: rate limit exceeded')
    }

    if (this.activeCount >= STT_MAX_CONCURRENCY) {
      throw new Error('STT is busy: maximum concurrency reached')
    }

    this.tokens -= 1
    this.activeCount += 1

    let released = false

    return () => {
      if (!released) {
        released = true
        this.activeCount = Math.max(0, this.activeCount - 1)
      }
    }
  }
}

class BoundedTtsQueue {
  private isProcessing = false
  private lastCloudTtsTime = 0
  private readonly queue: Array<() => Promise<void>> = []

  enqueue<T>(fn: () => Promise<T>): Promise<T> {
    if (this.queue.length >= TTS_MAX_QUEUE_SIZE) {
      throw new Error('TTS is busy: queue is full')
    }

    return new Promise<T>((resolve, reject) => {
      this.queue.push(async () => {
        try {
          resolve(await fn())
        } catch (error) {
          reject(error)
        }
      })
      void this.drain()
    })
  }

  async throttleCloud(): Promise<void> {
    const now = Date.now()
    const waitMs = MIN_TTS_INTERVAL_MS - (now - this.lastCloudTtsTime)

    if (waitMs > 0) {
      await sleep(waitMs)
    }

    this.lastCloudTtsTime = Date.now()
  }

  private async drain(): Promise<void> {
    if (this.isProcessing) {
      return
    }

    this.isProcessing = true

    try {
      for (let task = this.queue.shift(); task; task = this.queue.shift()) {
        await task()
      }
    } finally {
      this.isProcessing = false
    }
  }
}

function createMediaHttp(connection: BackendConnection, fetchImpl = globalThis.fetch): ApiTransport {
  return createHttpClient({
    fetch: fetchImpl,
    baseUrl: connection.baseUrl,
    headers: () => new Headers(connection.token ? { Authorization: `Bearer ${connection.token}` } : undefined)
  })
}

function formatKv(
  prefix: string,
  event: string,
  base: Record<string, unknown>,
  extra: Record<string, unknown> = {}
): string {
  const fmt = ([k, v]: [string, unknown]) => `${k}=${typeof v === 'string' ? JSON.stringify(v) : String(v)}`

  const parts = [...Object.entries(base), ...Object.entries(extra)]
    .filter(([, v]) => v !== null && v !== undefined && v !== false)
    .map(fmt)

  return `${prefix} ${event} ${parts.join(' ')}`
}

function makeLog(
  log: (msg: string) => void,
  prefix: string,
  base: Record<string, unknown>
): (event: string, extra?: Record<string, unknown>) => void {
  return (event, extra = {}) => log(formatKv(prefix, event, base, extra))
}

async function sttViaBackend({
  connection,
  data,
  fetchImpl,
  filename,
  language,
  mime
}: {
  connection: BackendConnection
  data: Buffer
  fetchImpl?: typeof globalThis.fetch
  filename?: string
  language?: string
  mime: string
}): Promise<string> {
  const form = new FormData()
  const blob = new Blob([data], { type: mime })

  const actualFilename =
    filename || (mime.includes('wav') ? 'audio.wav' : mime.includes('webm') ? 'audio.webm' : 'audio.wav')

  form.append('audio_file', blob, actualFilename)

  const parsed = await createMediaHttp(connection, fetchImpl).invoke(
    'media.speech_to_text',
    {
      body: form,
      query: language ? { language } : {}
    },
    { timeoutMs: 0 }
  )

  if (typeof parsed?.text !== 'string') {
    throw new Error('Backend STT returned no text')
  }

  return parsed.text
}

async function ttsViaBackend({
  connection,
  fetchImpl,
  language,
  text,
  voice
}: {
  connection: BackendConnection
  fetchImpl?: typeof globalThis.fetch
  language?: string
  text: string
  voice?: string
}): Promise<{ buffer: Buffer; mimeType: string; voiceOut?: string }> {
  let mimeType = 'audio/mpeg'
  let voiceOut: string | undefined

  const bytes = await createMediaHttp(connection, fetchImpl).invoke(
    'media.text_to_speech',
    {
      body: { text, ...(voice ? { voice } : {}), ...(language ? { language } : {}) }
    },
    {
      timeoutMs: 0,
      onResponse: response => {
        mimeType = response.headers.get('content-type') || 'audio/mpeg'
        voiceOut = response.headers.get('x-voice-used') || undefined
      }
    }
  )

  return {
    buffer: Buffer.from(bytes),
    mimeType,
    voiceOut
  }
}

function getCachedTts(key: string): null | { dataUrl: string; expiresAt: number; mimeType: string } {
  const entry = ttsAudioCache.get(key)

  if (!entry) {
    return null
  }

  if (Date.now() > entry.expiresAt) {
    ttsAudioCache.delete(key)

    return null
  }

  ttsAudioCache.delete(key)
  ttsAudioCache.set(key, entry)

  return entry
}

function setCachedTts(key: string, value: { dataUrl: string; mimeType: string }): void {
  if (ttsAudioCache.size >= TTS_CACHE_MAX_ENTRIES) {
    const oldestKey = ttsAudioCache.keys().next().value

    if (oldestKey !== undefined) {
      ttsAudioCache.delete(oldestKey)
    }
  }

  ttsAudioCache.set(key, { ...value, expiresAt: Date.now() + TTS_CACHE_TTL_MS })
}

interface MediaIpcDeps {
  spiritagentHome: string
  ensureBackend: () => Promise<BackendConnection>
  fetchImpl?: typeof globalThis.fetch
  getCurrentAuth: GetCurrentAuth
  getSelectedAccountId: () => string | null
  ipcMain: IpcMain
  log?: (msg: string) => void
}

export function registerMediaIpc({
  spiritagentHome,
  ensureBackend,
  fetchImpl,
  getCurrentAuth,
  getSelectedAccountId,
  ipcMain,
  log = () => {}
}: MediaIpcDeps): { clearAccountCache: (accountId: string) => Promise<void> } {
  const diskCache = createTtsDiskCache({ spiritagentHome })
  const sttLimiter = new SttLimiter()
  const ttsQueue = new BoundedTtsQueue()
  const callBackend = createBackendCaller({ ensureBackend, getCurrentAuth })
  const cacheEpochs = new Map<string, number>()

  const assertUploadAccount = (authSessionId: string): void => {
    if (!authSessionId || getCurrentAuth()?.sessionId !== authSessionId) {
      throw new Error('Attachment upload account changed')
    }
  }

  registerApiResult(
    ipcMain,
    IPC.invoke.mediaAudioUpload,
    async (event, payload: AudioUploadPayload): Promise<AudioUploadResult> => {
      const authSessionId = payload?.authSessionId
      assertUploadAccount(authSessionId)

      if (
        typeof payload?.dataUrl !== 'string' ||
        payload.dataUrl.length > Math.ceil((STT_MAX_AUDIO_BYTES * 4) / 3) + 256 ||
        !/^\d+$/.test(payload.sessionId)
      ) {
        throw new Error('Invalid audio upload')
      }

      const { data, mime } = parseDataUrl(payload.dataUrl)

      if (data.length === 0 || data.length > STT_MAX_AUDIO_BYTES || !mime.startsWith('audio/')) {
        throw new Error('Invalid audio upload')
      }

      const form = new FormData()
      form.append('file', new Blob([data], { type: mime }), path.basename(payload.filename))
      form.append('session_id', payload.sessionId)

      const parsed = await callBackend(event.sender, connection => {
        assertUploadAccount(authSessionId)

        return createMediaHttp(connection, fetchImpl).invoke(
          'media.upload_chat_audio',
          {
            body: form
          },
          { timeoutMs: ATTACH_VIDEO_TIMEOUT_MS }
        )
      })

      assertUploadAccount(authSessionId)

      if (
        !parsed ||
        typeof parsed !== 'object' ||
        !('url' in parsed) ||
        typeof parsed.url !== 'string' ||
        !('mime_type' in parsed) ||
        typeof parsed.mime_type !== 'string' ||
        !('duration' in parsed) ||
        typeof parsed.duration !== 'number' ||
        !Number.isFinite(parsed.duration) ||
        parsed.duration <= 0
      ) {
        throw new Error('Backend audio upload returned invalid metadata')
      }

      return { url: parsed.url, mime_type: parsed.mime_type, duration: parsed.duration }
    }
  )

  registerApiResult(
    ipcMain,
    IPC.invoke.mediaVoiceSampleUpload,
    async (event, payload: VoiceSampleUploadPayload): Promise<VoiceSampleUploadResult> => {
      const authSessionId = payload?.authSessionId
      assertUploadAccount(authSessionId)

      if (
        typeof payload?.dataUrl !== 'string' ||
        payload.dataUrl.length > Math.ceil((STT_MAX_AUDIO_BYTES * 4) / 3) + 256 ||
        typeof payload.filename !== 'string' ||
        !payload.filename.trim()
      ) {
        throw new Error('Invalid voice sample upload')
      }

      const { data, mime } = parseDataUrl(payload.dataUrl)

      if (data.length === 0 || data.length > STT_MAX_AUDIO_BYTES || !mime.startsWith('audio/')) {
        throw new Error('Invalid voice sample upload')
      }

      const form = new FormData()
      form.append('file', new Blob([data], { type: mime }), path.basename(payload.filename))

      const parsed = await callBackend(event.sender, connection => {
        assertUploadAccount(authSessionId)

        return createMediaHttp(connection, fetchImpl).invoke(
          'media.upload_voice_reference',
          {
            body: form
          },
          { timeoutMs: ATTACH_VIDEO_TIMEOUT_MS }
        )
      })

      assertUploadAccount(authSessionId)

      if (
        !parsed ||
        typeof parsed !== 'object' ||
        !('sample_id' in parsed) ||
        typeof parsed.sample_id !== 'string' ||
        !parsed.sample_id ||
        !('url' in parsed) ||
        typeof parsed.url !== 'string' ||
        !('mime_type' in parsed) ||
        typeof parsed.mime_type !== 'string' ||
        !('duration' in parsed) ||
        typeof parsed.duration !== 'number' ||
        !Number.isFinite(parsed.duration) ||
        parsed.duration <= 0
      ) {
        throw new Error('Backend voice sample upload returned invalid metadata')
      }

      return {
        sample_id: parsed.sample_id,
        url: parsed.url,
        mime_type: parsed.mime_type,
        duration: parsed.duration
      }
    }
  )

  registerApiResult(ipcMain, IPC.invoke.mediaStt, async (event, payload?: MediaSttPayload) => {
    const sttId = ++sttSeq
    const { data, mime } = parseDataUrl(payload?.dataUrl || '')

    if (data.length > STT_MAX_AUDIO_BYTES) {
      throw new Error(`Audio too large (${data.length} bytes; max ${STT_MAX_AUDIO_BYTES})`)
    }

    const release = sttLimiter.acquire()

    try {
      const startedAt = Date.now()

      const lang = resolveMediaLanguage(payload, DEFAULT_STT_LANGUAGE)

      const sttLog = makeLog(log, '[stt]', {
        bytes: data.length,
        id: sttId,
        lang,
        mime
      })

      sttLog('start')

      const text = await callBackend(event.sender, connection =>
        sttViaBackend({
          connection,
          data,
          fetchImpl,
          filename: payload?.filename,
          language: lang,
          mime
        })
      )

      sttLog('done', {
        chars: text.length,
        ms: Date.now() - startedAt,
        route: 'cloud'
      })

      return { text }
    } finally {
      release()
    }
  })

  registerApiResult(ipcMain, IPC.invoke.mediaTts, async (event, payload?: MediaTtsPayload) => {
    const ttsId = ++ttsSeq
    const text = speechText(String(payload?.text || ''))

    if (!text) {
      throw new Error('text is required')
    }

    if (text.length > TTS_MAX_TEXT_CHARS) {
      throw new Error(`Text too long (${text.length} chars; max ${TTS_MAX_TEXT_CHARS})`)
    }

    const voice = payload?.voice || ''
    const accountId = getSelectedAccountId()
    const authSessionId = getCurrentAuth()?.sessionId

    if (!accountId || !authSessionId) {
      throw new Error('An authenticated account is required for TTS')
    }

    const cacheEpoch = cacheEpochs.get(accountId) ?? 0

    const assertTtsAccount = (): void => {
      if (
        getSelectedAccountId() !== accountId ||
        getCurrentAuth()?.sessionId !== authSessionId ||
        (cacheEpochs.get(accountId) ?? 0) !== cacheEpoch
      ) {
        throw new Error('TTS account changed')
      }
    }

    // TTS 语言统一由主进程配置解析，渲染层不传。
    const language = resolveMediaLanguage(undefined, DEFAULT_TTS_LANGUAGE)
    const persist = payload?.persist === true
    const startedAt = Date.now()

    const ttsLog = makeLog(log, '[tts]', {
      chars: text.length,
      ctx: payload?.context || 'default',
      id: ttsId,
      lang: language,
      persist,
      voice: voice || null
    })

    ttsLog('start')

    const cacheKey = JSON.stringify([accountId, voice, language, text])
    const pendingKey = JSON.stringify([accountId, authSessionId, voice, language, text])
    assertTtsAccount()
    const cached = getCachedTts(cacheKey)

    if (cached) {
      ttsLog('done', { cached: true, ms: Date.now() - startedAt, route: 'memory' })

      return { dataUrl: cached.dataUrl, mimeType: cached.mimeType }
    }

    const pending = inflightTts.get(pendingKey)

    if (pending) {
      ttsLog('join', { route: 'inflight' })

      const value = await pending
      assertTtsAccount()

      return value
    }

    const task = (async () => {
      if (persist) {
        assertTtsAccount()
        const hit = await diskCache.read({ accountId, language, text, voice })
        assertTtsAccount()

        if (hit) {
          const value = { dataUrl: dataUrlFromBuffer(hit, 'audio/mpeg'), mimeType: 'audio/mpeg' }
          setCachedTts(cacheKey, value)
          ttsLog('done', { bytes: hit.length, cached: true, ms: Date.now() - startedAt, route: 'disk' })

          return value
        }
      }

      // 云端间隔只约束真实出网请求；磁盘命中不占云端额度。
      return ttsQueue.enqueue(async () => {
        assertTtsAccount()
        await ttsQueue.throttleCloud()
        assertTtsAccount()

        const result = await callBackend(event.sender, connection => {
          assertTtsAccount()

          return ttsViaBackend({ connection, fetchImpl, language, text, voice })
        })

        assertTtsAccount()

        const value = { dataUrl: dataUrlFromBuffer(result.buffer, result.mimeType), mimeType: result.mimeType }
        setCachedTts(cacheKey, value)
        let persisted = false

        if (persist) {
          try {
            persisted = await diskCache.write({
              accountId,
              buffer: result.buffer,
              language,
              mimeType: result.mimeType,
              text,
              voice
            })

            if (!persisted) {
              ttsLog('persist_skipped', { mime: result.mimeType })
            }
          } catch (error) {
            ttsLog('persist_failed', { error: errorMessage(error) })
          }
        }

        ttsLog('done', {
          mime: result.mimeType,
          ms: Date.now() - startedAt,
          persisted,
          route: 'cloud',
          voice_out: result.voiceOut || null
        })

        assertTtsAccount()

        return value
      })
    })()

    inflightTts.set(pendingKey, task)

    try {
      const value = await task
      assertTtsAccount()

      return value
    } finally {
      if (inflightTts.get(pendingKey) === task) {
        inflightTts.delete(pendingKey)
      }
    }
  })

  registerApiResult(
    ipcMain,
    IPC.invoke.mediaVideoUpload,
    async (event, payload: AttachmentVideoUploadPayload): Promise<AttachmentVideoUploadResult> => {
      const authSessionId = payload?.authSessionId
      assertUploadAccount(authSessionId)

      if (typeof payload?.path !== 'string' || !payload.path || !/^\d+$/.test(payload.sessionId)) {
        throw new Error('Invalid video upload')
      }

      const { data, resolvedPath } = await readUserSelectedFile(payload.path, {
        maxBytes: ATTACH_VIDEO_MAX_BYTES,
        purpose: 'Video attach'
      })

      assertUploadAccount(authSessionId)

      const form = new FormData()
      const blob = new Blob([data], { type: 'application/octet-stream' })

      form.append('file', blob, path.basename(resolvedPath))
      form.append('session_id', payload.sessionId)

      const parsed = await callBackend(event.sender, connection => {
        assertUploadAccount(authSessionId)

        return createMediaHttp(connection, fetchImpl).invoke(
          'media.upload_chat_video',
          {
            body: form
          },
          { timeoutMs: ATTACH_VIDEO_TIMEOUT_MS }
        )
      })

      assertUploadAccount(authSessionId)

      if (typeof parsed?.url !== 'string' || !parsed.url) {
        throw new Error('Backend video upload returned no url')
      }

      return { url: parsed.url }
    }
  )

  registerApiResult(
    ipcMain,
    IPC.invoke.mediaDesktopVideoUpload,
    async (event, payload: DesktopVideoUploadPayload): Promise<DesktopVideoUploadResult> => {
      const authSessionId = getCurrentAuth()?.sessionId

      if (!authSessionId || typeof payload?.authSessionId !== 'string' || authSessionId !== payload.authSessionId) {
        throw new Error('An authenticated session is required')
      }

      if (typeof payload?.path !== 'string' || !payload.path) {
        throw new Error('Desktop video file is required')
      }

      if (!Number.isInteger(payload?.actionId) || payload.actionId <= 0) {
        throw new Error('Desktop video action is invalid')
      }

      if (!Number.isInteger(payload?.expectedSetId) || payload.expectedSetId <= 0) {
        throw new Error('Desktop video set is invalid')
      }

      if (
        !['image', 'video'].includes(payload.mediaKind) ||
        !Number.isSafeInteger(payload.expectedActionVersion) ||
        payload.expectedActionVersion < 0 ||
        !Number.isSafeInteger(payload.expectedBaseVersion) ||
        payload.expectedBaseVersion < 0 ||
        !Number.isSafeInteger(payload.expectedResolutionRevision) ||
        payload.expectedResolutionRevision < 1 ||
        !['ask', 'keep', 'expand'].includes(payload.aspectPolicy) ||
        (payload.expectedDraftId !== null && typeof payload.expectedDraftId !== 'string')
      ) {
        throw new Error('Invalid desktop media context')
      }

      const { data, resolvedPath } = await readUserSelectedFile(payload.path, {
        maxBytes: payload.mediaKind === 'image' ? 20 * 1024 * 1024 : ATTACH_VIDEO_MAX_BYTES,
        purpose: 'Desktop media upload'
      })

      const form = new FormData()
      const mime = mimeTypeForPath(resolvedPath)
      const blob = new Blob([data], { type: mime })

      form.append('file', blob, path.basename(resolvedPath))
      form.append('expected_set_id', String(payload.expectedSetId))
      form.append('expected_action_version', String(payload.expectedActionVersion))
      form.append('expected_base_version', String(payload.expectedBaseVersion))
      form.append('expected_resolution_revision', String(payload.expectedResolutionRevision))
      form.append('aspect_policy', payload.aspectPolicy)
      form.append('expected_draft_id', payload.expectedDraftId ?? 'none')

      if (getCurrentAuth()?.sessionId !== authSessionId) {
        throw new Error('Authentication changed during desktop video upload')
      }

      const response = await callBackend(event.sender, connection => {
        assertUploadAccount(authSessionId)

        const client = createMediaHttp(connection, fetchImpl)
        const input = { path: { action_id: payload.actionId }, body: form }
        const options = { timeoutMs: DESKTOP_VIDEO_UPLOAD_TIMEOUT_MS }

        return payload.mediaKind === 'image'
          ? client.invoke('companion_desktop_videos.upload_image', input, options)
          : client.invoke('companion_desktop_videos.upload_action', input, options)
      })

      if (getCurrentAuth()?.sessionId !== authSessionId) {
        throw new Error('Authentication changed during desktop video upload')
      }

      return { response }
    }
  )

  return {
    clearAccountCache: async accountId => {
      cacheEpochs.set(accountId, (cacheEpochs.get(accountId) ?? 0) + 1)
      const prefix = `[${JSON.stringify(accountId)},`

      for (const key of ttsAudioCache.keys()) {
        if (key.startsWith(prefix)) {
          ttsAudioCache.delete(key)
        }
      }

      for (const key of inflightTts.keys()) {
        if (key.startsWith(prefix)) {
          inflightTts.delete(key)
        }
      }

      await diskCache.clear(accountId)
    }
  }
}
