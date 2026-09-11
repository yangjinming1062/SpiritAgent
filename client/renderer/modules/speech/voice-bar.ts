import type { SpeechStyle } from '@ipc/contracts'

import { safeJsonParse } from '@/shared/lib/safe-json'
import { registerCompanionStorageKey, registerStorageClearHandler } from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { $surfaceOpen, isLivingProxyWindow } from '@/shared/store/surfaces'

import { speechStyleKey } from '../../../shared/speech-style'
import { speechText } from '../../../shared/speech-text'

// 语音条播放引擎：合成缓存、时长缓存、自动接播队列与播放中断。
// 播放队列的真相在这里；消息气泡中的语音状态只是 UI 投影——引擎对消息体的
// 全部读写都经 bindVoiceBarProjection 注入（app/workflows/conversation-speech 装配），
// 本模块不导入会话模块。

export const TTS_MAX_TEXT_CHARS = 4000
const MAX_CACHED_DURATIONS = 500
const MAX_AUDIO_DATA_URL_ENTRIES = 100

const VOICE_DURATIONS_KEY = registerCompanionStorageKey('da.companion.voiceDurations', {
  preserveOnLogout: true
})

const audioDataUrlCache = new Map<string, string>()
const autoPlayQueue: string[] = []
const inflightResolutions = new Map<string, Promise<{ dataUrl: string; duration: number } | undefined>>()

function setCachedAudioDataUrl(key: string, dataUrl: string): void {
  if (audioDataUrlCache.size >= MAX_AUDIO_DATA_URL_ENTRIES) {
    const oldestKey = audioDataUrlCache.keys().next().value

    if (oldestKey !== undefined) {
      audioDataUrlCache.delete(oldestKey)
    }
  }

  audioDataUrlCache.set(key, dataUrl)
}

let synthEpoch = 0
let activePlayToken = 0
let memoryDurationCache: Record<string, number> | null = null

export interface VoiceBarProjection {
  getVoiceItem(messageId: string): { speechStyle?: SpeechStyle; text: string } | undefined
  patchVoice(
    messageId: string,
    patch: { voiceDuration?: number; voiceStatus?: 'failed' | 'pending' | 'ready' | undefined }
  ): void
  setPlaying(messageId: string | null): void
  setLoading(messageId: string | null): void
  getPlaying(): string | null
  failPendingWithoutDuration(): void
  hasPendingVoice(exceptId?: string): boolean
  clearProjectedDurations(): void
}

let projection: VoiceBarProjection | null = null

export function bindVoiceBarProjection(next: VoiceBarProjection): void {
  projection = next
}

function voiceProjection(): VoiceBarProjection {
  if (!projection) {
    throw new Error('voice bar projection not bound — app bootstrap must initialize first')
  }

  return projection
}

export function isLivingVoiceBarActive(): boolean {
  const ports = presentationPorts()

  return isLivingProxyWindow() && ports.$responseMode.get() === 'voice' && !ports.$screenLocked.get()
}

function canAutoPlayVoiceBar(): boolean {
  return isLivingVoiceBarActive() && $surfaceOpen.get() === 'living'
}

function hasOutstandingVoice(exceptId?: string): boolean {
  const playing = voiceProjection().getPlaying()

  if (playing && playing !== exceptId) {
    return true
  }

  if (autoPlayQueue.some(id => id !== exceptId)) {
    return true
  }

  return voiceProjection().hasPendingVoice(exceptId)
}

function getDurationMap(): Record<string, number> {
  if (memoryDurationCache !== null) {
    return memoryDurationCache
  }

  try {
    const raw = typeof window !== 'undefined' ? window.localStorage.getItem(VOICE_DURATIONS_KEY) : null
    memoryDurationCache = safeJsonParse(raw, {}) as Record<string, number>
  } catch {
    memoryDurationCache = {}
  }

  return memoryDurationCache
}

export function getCachedVoiceDuration(text: string, speechStyle?: SpeechStyle): number | undefined {
  const trimmed = speechText(text)

  if (!trimmed) {
    return undefined
  }

  const map = getDurationMap()
  const val = map[voiceCacheKey(trimmed, speechStyle)]

  return typeof val === 'number' && val > 0 ? val : undefined
}

export function setCachedVoiceDuration(
  text: string,
  duration: number,
  speechStyle?: SpeechStyle,
  voice = presentationPorts().$companionVoiceId.get()
): void {
  const trimmed = speechText(text)

  if (!trimmed || typeof duration !== 'number' || duration <= 0) {
    return
  }

  const map = getDurationMap()

  if (map[voiceCacheKey(trimmed, speechStyle, voice)] === duration) {
    return
  }

  const keys = Object.keys(map)

  if (keys.length >= MAX_CACHED_DURATIONS) {
    for (let i = 0; i < keys.length - MAX_CACHED_DURATIONS + 1; i++) {
      delete map[keys[i]]
    }
  }

  map[voiceCacheKey(trimmed, speechStyle, voice)] = duration

  try {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(VOICE_DURATIONS_KEY, JSON.stringify(map))
    }
  } catch {
    // 尽力而为
  }
}

function voiceCacheKey(
  text: string,
  speechStyle?: SpeechStyle,
  voice = presentationPorts().$companionVoiceId.get()
): string {
  return JSON.stringify([voice, speechText(text), speechStyleKey(speechStyle)])
}

export async function resolveVoiceBarDuration(messageId: string, text: string): Promise<number | undefined> {
  const trimmed = speechText(text)

  if (!trimmed || trimmed.length > TTS_MAX_TEXT_CHARS) {
    return undefined
  }

  const epoch = synthEpoch
  const speechStyle = voiceProjection().getVoiceItem(messageId)?.speechStyle
  const voice = presentationPorts().$companionVoiceId.get()
  const resolutionKey = voiceCacheKey(trimmed, speechStyle, voice)
  const cached = getCachedVoiceDuration(trimmed, speechStyle)

  if (cached && audioDataUrlCache.has(resolutionKey)) {
    voiceProjection().patchVoice(messageId, { voiceDuration: cached, voiceStatus: 'ready' })

    return cached
  }

  const inflightKey = JSON.stringify([epoch, resolutionKey])
  let task = inflightResolutions.get(inflightKey)

  if (!task) {
    task = (async (): Promise<{ dataUrl: string; duration: number } | undefined> => {
      try {
        const dataUrl = await presentationPorts().requestSynth(trimmed, voice, 'chat.replay', true, speechStyle)
        const duration = await measureAudioDuration(dataUrl)

        if (epoch !== synthEpoch || typeof duration !== 'number' || duration <= 0) {
          return undefined
        }

        setCachedAudioDataUrl(resolutionKey, dataUrl)
        setCachedVoiceDuration(trimmed, duration, speechStyle, voice)

        return { dataUrl, duration }
      } catch (err) {
        console.warn('[voice-bar] resolveVoiceBarDuration failed:', err)

        return undefined
      } finally {
        inflightResolutions.delete(inflightKey)
      }
    })()
    inflightResolutions.set(inflightKey, task)
  }

  const result = await task

  if (result && epoch === synthEpoch && isCurrentVoice(messageId, resolutionKey)) {
    voiceProjection().patchVoice(messageId, { voiceDuration: result.duration, voiceStatus: 'ready' })

    return result.duration
  }

  return undefined
}

function isCurrentVoice(messageId: string, key: string): boolean {
  const item = voiceProjection().getVoiceItem(messageId)

  return !!item && voiceCacheKey(item.text.trim(), item.speechStyle) === key
}

function applyTurnEndState(): void {
  const ports = presentationPorts()

  if (ports.$spriteState.get() === 'speaking' || ports.$spriteState.get() === 'thinking') {
    ports.setSpriteState('idle', { force: true })
  }
}

function playNextOrFinish(): void {
  if (autoPlayQueue.length > 0) {
    const nextId = autoPlayQueue.shift()!
    void playVoiceBar(nextId)

    return
  }

  applyTurnEndState()
}

function measureAudioDuration(dataUrl: string): Promise<number | null> {
  return new Promise(resolve => {
    const audio = new Audio()
    audio.preload = 'metadata'

    let timer: ReturnType<typeof setTimeout> | null = null
    let settled = false

    const finish = (seconds: number | null): void => {
      if (settled) {
        return
      }

      settled = true

      if (timer !== null) {
        clearTimeout(timer)
        timer = null
      }

      audio.removeEventListener('loadedmetadata', onLoaded)
      audio.removeEventListener('durationchange', onLoaded)
      audio.removeEventListener('error', onError)
      audio.removeAttribute('src')
      audio.load()
      resolve(seconds)
    }

    const checkDuration = (): boolean => {
      const raw = audio.duration

      if (Number.isFinite(raw) && raw > 0) {
        finish(Math.max(1, Math.round(raw)))

        return true
      }

      return false
    }

    const onLoaded = (): void => {
      checkDuration()
    }

    const onError = (): void => {
      finish(null)
    }

    timer = setTimeout(() => {
      if (!checkDuration()) {
        finish(null)
      }
    }, 4000)

    audio.addEventListener('loadedmetadata', onLoaded)
    audio.addEventListener('durationchange', onLoaded)
    audio.addEventListener('error', onError)
    audio.src = dataUrl
  })
}

export async function synthesizeVoiceBar(
  messageId: string,
  text: string,
  options?: { autoPlay?: boolean }
): Promise<void> {
  const trimmed = speechText(text)

  if (!trimmed) {
    return
  }

  const proj = voiceProjection()

  if (trimmed.length > TTS_MAX_TEXT_CHARS) {
    proj.patchVoice(messageId, { voiceStatus: 'failed' })

    if (!hasOutstandingVoice(messageId)) {
      applyTurnEndState()
    }

    return
  }

  const epoch = synthEpoch
  proj.patchVoice(messageId, { voiceStatus: 'pending' })

  const key = voiceCacheKey(trimmed, proj.getVoiceItem(messageId)?.speechStyle)
  const duration = await resolveVoiceBarDuration(messageId, trimmed)

  if (epoch !== synthEpoch || !isCurrentVoice(messageId, key)) {
    return
  }

  if (duration === undefined) {
    proj.patchVoice(messageId, { voiceStatus: 'failed' })

    if (!hasOutstandingVoice(messageId)) {
      applyTurnEndState()
    }

    return
  }

  if (options?.autoPlay && canAutoPlayVoiceBar()) {
    if (voiceProjection().getPlaying() === null) {
      void playVoiceBar(messageId)
    } else if (!autoPlayQueue.includes(messageId)) {
      autoPlayQueue.push(messageId)
    }
  } else if (!hasOutstandingVoice(messageId)) {
    applyTurnEndState()
  }
}

async function playVoiceBar(messageId: string, isManualClick = false): Promise<void> {
  const ports = presentationPorts()

  if (ports.$screenLocked.get()) {
    return
  }

  if (isManualClick) {
    autoPlayQueue.length = 0
  }

  if (voiceProjection().getPlaying() === messageId) {
    stopVoiceBar()

    return
  }

  stopVoiceBar()

  const playToken = ++activePlayToken
  const item = voiceProjection().getVoiceItem(messageId)
  const text = speechText(item?.text ?? '')

  if (!text || text.length > TTS_MAX_TEXT_CHARS) {
    voiceProjection().patchVoice(messageId, { voiceStatus: 'failed' })

    return
  }

  const key = voiceCacheKey(text, item?.speechStyle)
  let dataUrl = audioDataUrlCache.get(key)

  if (!dataUrl) {
    voiceProjection().setLoading(messageId)
    await resolveVoiceBarDuration(messageId, text)

    if (playToken !== activePlayToken) {
      return
    }

    voiceProjection().setLoading(null)
    dataUrl = audioDataUrlCache.get(key)
  }

  if (playToken !== activePlayToken || !isCurrentVoice(messageId, key)) {
    return
  }

  if (!dataUrl) {
    voiceProjection().patchVoice(messageId, { voiceStatus: 'failed' })

    return
  }

  voiceProjection().setPlaying(messageId)
  ports.setSpriteState('speaking')

  const onDone = (): void => {
    if (playToken === activePlayToken && voiceProjection().getPlaying() === messageId) {
      voiceProjection().setPlaying(null)
      playNextOrFinish()
    }
  }

  const ok = await ports.playDataUrl(dataUrl, onDone)

  if (!ok && playToken === activePlayToken && voiceProjection().getPlaying() === messageId) {
    voiceProjection().setPlaying(null)
    playNextOrFinish()
  }
}

function stopVoiceBar(): void {
  const ports = presentationPorts()

  activePlayToken++
  voiceProjection().setPlaying(null)
  voiceProjection().setLoading(null)
  ports.stopAudio()

  if (ports.$spriteState.get() === 'speaking') {
    ports.setSpriteState('idle', { force: true })
  }
}

export function cancelVoiceBar(): void {
  const ports = presentationPorts()

  activePlayToken++
  synthEpoch++
  autoPlayQueue.length = 0
  voiceProjection().setPlaying(null)
  voiceProjection().setLoading(null)
  ports.stopAudio()
  voiceProjection().failPendingWithoutDuration()

  if (ports.$spriteState.get() === 'speaking' || ports.$spriteState.get() === 'thinking') {
    ports.setSpriteState('idle', { force: true })
  }
}

// 模块顶层访问 presentationPorts 会撞上「会话先于端口绑定」的加载顺序：
// 这几个 listen 在端口绑定完成后由 app/bootstrap 显式注册。
export function bindVoiceBarListeners(): void {
  const ports = presentationPorts()

  ports.$screenLocked.listen(locked => {
    if (locked) {
      cancelVoiceBar()
    }
  })

  ports.$companionVoiceId.listen(() => {
    cancelVoiceBar()
    voiceProjection().clearProjectedDurations()
  })

  ports.$responseMode.listen(mode => {
    if (mode !== 'voice') {
      cancelVoiceBar()
    }
  })
}

registerStorageClearHandler(() => {
  memoryDurationCache = null
  audioDataUrlCache.clear()
  cancelVoiceBar()
})

export function estimateVoiceDuration(text: string): number {
  const trimmed = speechText(text)

  if (!trimmed) {
    return 1
  }

  const clean = trimmed.replace(/\s+/g, ' ')
  const cjkMatches = clean.match(/[\u4e00-\u9fa5]/g)
  const cjkCount = cjkMatches ? cjkMatches.length : 0
  const nonCjk = clean.replace(/[\u4e00-\u9fa5]/g, '').trim()
  const wordCount = nonCjk ? nonCjk.split(/\s+/).filter(Boolean).length : 0

  const estimatedSeconds = cjkCount / 3.8 + wordCount / 2.5

  return Math.max(1, Math.min(60, Math.round(estimatedSeconds)))
}

/** 语音条点击入口：播放中则停止，否则合成/取缓存并播放（手动点击清空接播队列）。 */
export function toggleVoiceBar(messageId: string): Promise<void> {
  return playVoiceBar(messageId, true)
}
