import fs from 'node:fs'
import path from 'node:path'

import {
  type AttachmentVideoUploadPayload,
  type AttachmentVideoUploadResult,
  IPC,
  type MediaSttPayload,
  type MediaTtsPayload
} from '@ipc/contracts'
import { sleep } from '@runtime'
import type { IpcMain } from 'electron'

import { type SpeechStyle, speechStyleKey } from '../../shared/speech-style'
import { speechText } from '../../shared/speech-text'
import { resolveReadableFileForIpc } from '../security/hardening'
import { assertUserSelectedPath } from '../security/user-selected-paths'
import * as store from '../shared/lib/runner-config-store'
import { dataUrlFromBuffer, dataUrlToBuffer, parseDataUrl } from '../shared/mime'

import { createTtsDiskCache } from './tts-disk-cache'

const STT_TIMEOUT_MS = 60_000
const TTS_TIMEOUT_MS = 60_000
const TTS_MAX_TEXT_CHARS = 4000
const STT_MAX_AUDIO_BYTES = 24 * 1024 * 1024
const ATTACH_VIDEO_MAX_BYTES = 512 * 1024 * 1024
const ATTACH_VIDEO_TIMEOUT_MS = 120_000
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

interface TtsQueueItem<T> {
  fn: () => Promise<T>
  reject: (err: unknown) => void
  resolve: (value: T) => void
}

class SttLimiter {
  private activeCount = 0
  private readonly burst: number
  private lastRefill: number
  private readonly maxConcurrency: number
  private readonly refillRate: number
  private tokens: number

  constructor() {
    this.maxConcurrency = STT_MAX_CONCURRENCY
    this.burst = STT_BURST
    this.refillRate = STT_REFILL_RATE
    this.tokens = STT_BURST
    this.lastRefill = Date.now()
  }

  acquire(): () => void {
    const now = Date.now()
    const elapsed = (now - this.lastRefill) / 1000
    this.tokens = Math.min(this.burst, this.tokens + elapsed * this.refillRate)
    this.lastRefill = now

    if (this.tokens < 1) {
      throw new Error('STT is busy: rate limit exceeded')
    }

    if (this.activeCount >= this.maxConcurrency) {
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
  private readonly maxQueueSize: number
  private readonly minCloudIntervalMs: number
  private readonly queue: Array<TtsQueueItem<unknown>> = []

  constructor() {
    this.maxQueueSize = TTS_MAX_QUEUE_SIZE
    this.minCloudIntervalMs = MIN_TTS_INTERVAL_MS
  }

  enqueue<T>(fn: () => Promise<T>): Promise<T> {
    if (this.queue.length >= this.maxQueueSize) {
      throw new Error('TTS is busy: queue is full')
    }

    return new Promise<T>((resolve, reject) => {
      this.queue.push({
        fn: fn as () => Promise<unknown>,
        reject,
        resolve: resolve as (value: unknown) => void
      })
      void this.processNext()
    })
  }

  async throttleCloud(): Promise<void> {
    const now = Date.now()
    const waitMs = this.minCloudIntervalMs - (now - this.lastCloudTtsTime)

    if (waitMs > 0) {
      await sleep(waitMs)
    }

    this.lastCloudTtsTime = Date.now()
  }

  private async processNext(): Promise<void> {
    if (this.isProcessing || this.queue.length === 0) {
      return
    }

    this.isProcessing = true
    const item = this.queue.shift()

    if (!item) {
      this.isProcessing = false

      return
    }

    try {
      const result = await item.fn()
      item.resolve(result)
    } catch (err) {
      item.reject(err)
    } finally {
      this.isProcessing = false
      void this.processNext()
    }
  }
}

function decodeDataUrl(dataUrl?: string): { data: Buffer; mime: string } {
  const parsed = parseDataUrl(dataUrl || '')

  return { data: parsed.data, mime: parsed.mime }
}

async function postMultipart({
  fetchImpl,
  form,
  timeoutMs,
  token,
  url
}: {
  fetchImpl?: typeof globalThis.fetch
  form: FormData
  timeoutMs: number
  token?: string
  url: string
}): Promise<{ body: Buffer; contentType: string; headers: Headers }> {
  const caller = fetchImpl || globalThis.fetch

  const res = await caller(url, {
    body: form,
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    method: 'POST',
    signal: AbortSignal.timeout(timeoutMs)
  })

  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status} ${new URL(url).pathname}: ${text || res.statusText}`)
  }

  const buf = Buffer.from(await res.arrayBuffer())

  return { body: buf, contentType: res.headers.get('content-type') || '', headers: res.headers }
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
  data,
  ensureBackend,
  fetchImpl = globalThis.fetch,
  filename,
  language,
  mime
}: {
  data: Buffer
  ensureBackend: () => Promise<{ baseUrl: string; token?: null | string }>
  fetchImpl?: typeof globalThis.fetch
  filename?: string
  language?: string
  mime: string
}): Promise<string> {
  const connection = await ensureBackend()
  const form = new FormData()
  const blob = new Blob([data], { type: mime })

  const actualFilename =
    filename || (mime.includes('wav') ? 'audio.wav' : mime.includes('webm') ? 'audio.webm' : 'audio.wav')

  form.append('audio_file', blob, actualFilename)

  const qs = language ? `?language=${encodeURIComponent(language)}` : ''
  const url = `${connection.baseUrl}/api/media/stt${qs}`

  const { body } = await postMultipart({
    fetchImpl,
    form,
    timeoutMs: STT_TIMEOUT_MS,
    token: connection.token || undefined,
    url
  })

  const parsed = JSON.parse(body.toString('utf8')) as { text?: string }

  if (typeof parsed?.text !== 'string') {
    throw new Error('Backend STT returned no text')
  }

  return parsed.text
}

async function ttsViaBackend({
  ensureBackend,
  fetchImpl,
  language,
  text,
  voice,
  speechStyle
}: {
  ensureBackend: () => Promise<{ baseUrl: string; token?: null | string }>
  fetchImpl?: typeof globalThis.fetch
  language?: string
  text: string
  voice?: string
  speechStyle?: SpeechStyle
}): Promise<{ dataUrl: string; mimeType: string; voiceOut?: string }> {
  const connection = await ensureBackend()
  const url = `${connection.baseUrl}/api/media/tts`

  const payload: Record<string, unknown> = { text, speech_style: speechStyle }

  if (voice) {
    payload.voice = voice
  }

  if (language) {
    payload.language = language
  }

  const caller = fetchImpl || globalThis.fetch

  const res = await caller(url, {
    body: JSON.stringify(payload),
    headers: {
      'Content-Type': 'application/json',
      ...(connection.token ? { Authorization: `Bearer ${connection.token}` } : {})
    },
    method: 'POST',
    signal: AbortSignal.timeout(TTS_TIMEOUT_MS)
  })

  if (!res.ok) {
    const errText = await res.text().catch(() => '')
    throw new Error(`${res.status} /api/media/tts: ${errText || res.statusText}`)
  }

  const mime = res.headers.get('content-type') || 'audio/mpeg'
  const buf = Buffer.from(await res.arrayBuffer())
  // 后端实际返回的头名是 X-Voice-Used（api/v1/media.py TTS 端点）。
  const voiceOut = res.headers.get('x-voice-used') || undefined

  return { dataUrl: dataUrlFromBuffer(buf, mime), mimeType: mime, voiceOut }
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
  spiritagentHome?: null | string
  ensureBackend: () => Promise<{ baseUrl: string; token?: null | string }>
  fetchImpl?: typeof globalThis.fetch
  ipcMain: IpcMain
  log?: (msg: string) => void
}

export function registerMediaIpc({
  spiritagentHome,
  ensureBackend,
  fetchImpl,
  ipcMain,
  log = () => {}
}: MediaIpcDeps): void {
  const diskCache = createTtsDiskCache({ spiritagentHome })
  const sttLimiter = new SttLimiter()
  const ttsQueue = new BoundedTtsQueue()

  ipcMain.handle(IPC.invoke.mediaStt, async (_event, payload?: MediaSttPayload) => {
    const sttId = ++sttSeq
    const { data, mime } = decodeDataUrl(payload?.dataUrl)

    if (data.length > STT_MAX_AUDIO_BYTES) {
      throw new Error(`Audio too large (${data.length} bytes; max ${STT_MAX_AUDIO_BYTES})`)
    }

    const release = sttLimiter.acquire()

    try {
      const startedAt = Date.now()

      const lang = resolveMediaLanguage(payload, DEFAULT_STT_LANGUAGE)

      const sttLog = makeLog(log, '[stt]', {
        bytes: data.length,
        ctx: payload?.context || 'default',
        id: sttId,
        lang,
        mime
      })

      sttLog('start')

      const text = await sttViaBackend({
        data,
        ensureBackend,
        fetchImpl,
        filename: payload?.filename,
        language: lang,
        mime
      })

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

  ipcMain.handle(IPC.invoke.mediaTts, async (_event, payload?: MediaTtsPayload) => {
    const ttsId = ++ttsSeq
    const text = speechText(String(payload?.text || ''))

    if (!text) {
      throw new Error('text is required')
    }

    if (text.length > TTS_MAX_TEXT_CHARS) {
      throw new Error(`Text too long (${text.length} chars; max ${TTS_MAX_TEXT_CHARS})`)
    }

    const voice = payload?.voice || ''
    const speechStyle = payload?.speech_style
    const styleKey = speechStyleKey(speechStyle)
    const diskVoice = styleKey ? JSON.stringify([voice, styleKey]) : voice
    const language = resolveMediaLanguage(payload, DEFAULT_TTS_LANGUAGE)
    const persist = payload?.persist === true
    const startedAt = Date.now()

    const ttsLog = makeLog(log, '[tts]', {
      chars: text.length,
      ctx: payload?.context || 'default',
      id: ttsId,
      lang: language,
      persisted: persist,
      voice: voice || null
    })

    ttsLog('start')

    const cacheKey = JSON.stringify([voice, language, text, styleKey])
    const cached = getCachedTts(cacheKey)

    if (cached) {
      ttsLog('done', { cached: true, ms: Date.now() - startedAt, route: 'memory' })

      return { dataUrl: cached.dataUrl, mimeType: cached.mimeType }
    }

    const pending = inflightTts.get(cacheKey)

    if (pending) {
      ttsLog('join', { route: 'inflight' })

      return await pending
    }

    const throttleCloud = () => ttsQueue.throttleCloud()

    const task = ttsQueue.enqueue(async () => {
      if (persist) {
        const hit = await diskCache.read({ language, text, voice: diskVoice })

        if (hit) {
          const value = { dataUrl: dataUrlFromBuffer(hit, 'audio/mpeg'), mimeType: 'audio/mpeg' }
          setCachedTts(cacheKey, value)
          ttsLog('done', { bytes: hit.length, cached: true, ms: Date.now() - startedAt, route: 'disk' })

          return value
        }
      }

      // 云端间隔只约束真实出网请求；磁盘命中不占云端额度。
      await throttleCloud()

      const result = await ttsViaBackend({ ensureBackend, fetchImpl, language, text, voice, speechStyle })
      const value = { dataUrl: result.dataUrl, mimeType: result.mimeType }
      setCachedTts(cacheKey, value)

      if (persist) {
        await diskCache.write({
          buffer: dataUrlToBuffer(result.dataUrl),
          language,
          mimeType: result.mimeType,
          text,
          voice: diskVoice
        })
      }

      ttsLog('done', {
        mime: result.mimeType,
        ms: Date.now() - startedAt,
        persisted: persist,
        route: 'cloud',
        voice_out: result.voiceOut || null
      })

      return value
    })

    inflightTts.set(cacheKey, task)

    try {
      return await task
    } finally {
      inflightTts.delete(cacheKey)
    }
  })

  ipcMain.handle(
    IPC.invoke.mediaVideoUpload,
    async (_event, payload: AttachmentVideoUploadPayload): Promise<AttachmentVideoUploadResult> => {
      assertUserSelectedPath(payload.path, 'Video attach')

      const { resolvedPath } = await resolveReadableFileForIpc(payload.path, {
        maxBytes: ATTACH_VIDEO_MAX_BYTES,
        purpose: 'Video attach'
      })

      const data = await fs.promises.readFile(resolvedPath)
      const connection = await ensureBackend()
      const form = new FormData()
      const blob = new Blob([data], { type: 'application/octet-stream' })

      form.append('file', blob, path.basename(resolvedPath))
      form.append('session_id', payload.sessionId)

      const { body } = await postMultipart({
        fetchImpl,
        form,
        timeoutMs: ATTACH_VIDEO_TIMEOUT_MS,
        token: connection.token || undefined,
        url: `${connection.baseUrl}/api/media/videos`
      })

      const parsed = JSON.parse(body.toString('utf8')) as {
        file_id?: string
        mime?: string
        size?: number
        url?: string
      }

      if (typeof parsed?.url !== 'string' || !parsed.url) {
        throw new Error('Backend video upload returned no url')
      }

      return {
        fileId: parsed.file_id || '',
        mime: parsed.mime || 'video/mp4',
        size: parsed.size ?? data.length,
        url: parsed.url
      }
    }
  )
}
