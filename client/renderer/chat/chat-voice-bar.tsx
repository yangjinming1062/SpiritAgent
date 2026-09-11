import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import type React from 'react'
import { useEffect, useState } from 'react'

import { ChevronDown, FileText, Loader2, Volume2 } from '@/shared/lib/icons'
import { safeJsonParse } from '@/shared/lib/safe-json'
import { registerCompanionStorageKey, registerStorageClearHandler } from '@/shared/lib/storage'
import { cn } from '@/shared/lib/utils'
import { presentationPorts } from '@/shared/presentation-ports'
import { $surfaceOpen, isLivingProxyWindow } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

import { type SpeechStyle, speechStyleKey } from '../../shared/speech-style'
import { speechText } from '../../shared/speech-text'

import { $chatMessageBodies, type ChatMessageBody } from './chat-store'

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

const $voiceBarPlayingId = atom<string | null>(null)
const $voiceBarLoadingId = atom<string | null>(null)

let synthEpoch = 0
let activePlayToken = 0
let memoryDurationCache: Record<string, number> | null = null

export function isLivingVoiceBarActive(): boolean {
  const ports = presentationPorts()

  return isLivingProxyWindow() && ports.$responseMode.get() === 'voice' && !ports.$screenLocked.get()
}

function canAutoPlayVoiceBar(): boolean {
  return isLivingVoiceBarActive() && $surfaceOpen.get() === 'living'
}

function hasOutstandingVoice(exceptId?: string): boolean {
  if ($voiceBarPlayingId.get() && $voiceBarPlayingId.get() !== exceptId) {
    return true
  }

  if (autoPlayQueue.some(id => id !== exceptId)) {
    return true
  }

  return Object.entries($chatMessageBodies.get()).some(
    ([id, body]) => id !== exceptId && body?.voiceStatus === 'pending'
  )
}

function failPendingVoiceBars(): void {
  for (const [id, body] of Object.entries($chatMessageBodies.get())) {
    if (body?.voiceStatus === 'pending' && body.voiceDuration == null) {
      $chatMessageBodies.setKey(id, { ...body, voiceStatus: undefined })
    }
  }
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
  const speechStyle = $chatMessageBodies.get()[messageId]?.speechStyle
  const voice = presentationPorts().$companionVoiceId.get()
  const resolutionKey = voiceCacheKey(trimmed, speechStyle, voice)
  const cached = getCachedVoiceDuration(trimmed, speechStyle)

  if (cached && audioDataUrlCache.has(resolutionKey)) {
    updateMessageVoice(messageId, { voiceDuration: cached, voiceStatus: 'ready' })

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
        console.warn('[chat-voice-bar] resolveVoiceBarDuration failed:', err)

        return undefined
      } finally {
        inflightResolutions.delete(inflightKey)
      }
    })()
    inflightResolutions.set(inflightKey, task)
  }

  const result = await task

  if (result && epoch === synthEpoch && isCurrentVoice(messageId, resolutionKey)) {
    updateMessageVoice(messageId, { voiceDuration: result.duration, voiceStatus: 'ready' })

    return result.duration
  }

  return undefined
}

function isCurrentVoice(messageId: string, key: string): boolean {
  const body = $chatMessageBodies.get()[messageId]

  return !!body && voiceCacheKey(body.text.trim(), body.speechStyle) === key
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

function updateMessageVoice(messageId: string, patch: Partial<ChatMessageBody>): void {
  const current = $chatMessageBodies.get()[messageId]

  if (current) {
    $chatMessageBodies.setKey(messageId, { ...current, ...patch })
  }
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

  if (trimmed.length > TTS_MAX_TEXT_CHARS) {
    updateMessageVoice(messageId, { voiceStatus: 'failed' })

    if (!hasOutstandingVoice(messageId)) {
      applyTurnEndState()
    }

    return
  }

  const epoch = synthEpoch
  updateMessageVoice(messageId, { voiceStatus: 'pending' })

  const key = voiceCacheKey(trimmed, $chatMessageBodies.get()[messageId]?.speechStyle)
  const duration = await resolveVoiceBarDuration(messageId, trimmed)

  if (epoch !== synthEpoch || !isCurrentVoice(messageId, key)) {
    return
  }

  if (duration === undefined) {
    updateMessageVoice(messageId, { voiceStatus: 'failed' })

    if (!hasOutstandingVoice(messageId)) {
      applyTurnEndState()
    }

    return
  }

  if (options?.autoPlay && canAutoPlayVoiceBar()) {
    if ($voiceBarPlayingId.get() === null) {
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

  if ($voiceBarPlayingId.get() === messageId) {
    stopVoiceBar()

    return
  }

  stopVoiceBar()

  const playToken = ++activePlayToken
  const body = $chatMessageBodies.get()[messageId]
  const text = speechText(body?.text ?? '')

  if (!text || text.length > TTS_MAX_TEXT_CHARS) {
    updateMessageVoice(messageId, { voiceStatus: 'failed' })

    return
  }

  const key = voiceCacheKey(text, body?.speechStyle)
  let dataUrl = audioDataUrlCache.get(key)

  if (!dataUrl) {
    $voiceBarLoadingId.set(messageId)
    await resolveVoiceBarDuration(messageId, text)

    if (playToken !== activePlayToken) {
      return
    }

    $voiceBarLoadingId.set(null)
    dataUrl = audioDataUrlCache.get(key)
  }

  if (playToken !== activePlayToken || !isCurrentVoice(messageId, key)) {
    return
  }

  if (!dataUrl) {
    updateMessageVoice(messageId, { voiceStatus: 'failed' })

    return
  }

  $voiceBarPlayingId.set(messageId)
  ports.setSpriteState('speaking')

  const onDone = (): void => {
    if (playToken === activePlayToken && $voiceBarPlayingId.get() === messageId) {
      $voiceBarPlayingId.set(null)
      playNextOrFinish()
    }
  }

  const ok = await ports.playDataUrl(dataUrl, onDone)

  if (!ok && playToken === activePlayToken && $voiceBarPlayingId.get() === messageId) {
    $voiceBarPlayingId.set(null)
    playNextOrFinish()
  }
}

function stopVoiceBar(): void {
  const ports = presentationPorts()

  activePlayToken++
  $voiceBarPlayingId.set(null)
  $voiceBarLoadingId.set(null)
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
  $voiceBarPlayingId.set(null)
  $voiceBarLoadingId.set(null)
  ports.stopAudio()
  failPendingVoiceBars()

  if (ports.$spriteState.get() === 'speaking' || ports.$spriteState.get() === 'thinking') {
    ports.setSpriteState('idle', { force: true })
  }
}

presentationPorts().$screenLocked.listen(locked => {
  if (locked) {
    cancelVoiceBar()
  }
})

presentationPorts().$companionVoiceId.listen(() => {
  cancelVoiceBar()

  for (const [id, body] of Object.entries($chatMessageBodies.get())) {
    if (body?.voiceDuration != null) {
      updateMessageVoice(id, { voiceDuration: undefined, voiceStatus: undefined })
    }
  }
})

presentationPorts().$responseMode.listen(mode => {
  if (mode !== 'voice') {
    cancelVoiceBar()
  }
})

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

interface ChatVoiceBarProps {
  duration?: number
  messageId: string
  text?: string
}

export function ChatVoiceBar({ duration, messageId, text }: ChatVoiceBarProps): React.JSX.Element {
  const dict = useStrings()
  const playingId = useStore($voiceBarPlayingId)
  const loadingId = useStore($voiceBarLoadingId)

  const isPlaying = playingId === messageId
  const isLoading = loadingId === messageId

  useEffect(() => {
    if (typeof duration === 'number' && duration > 0) {
      return
    }

    if (!text?.trim()) {
      return
    }

    void resolveVoiceBarDuration(messageId, text)
  }, [duration, messageId, text])

  const hasRealDuration = typeof duration === 'number' && duration > 0
  const effectiveSec = hasRealDuration ? duration : text ? estimateVoiceDuration(text) : 1
  const sec = Math.max(1, Math.min(60, effectiveSec))
  const widthPx = 76 + Math.round(((sec - 1) / 59) * (220 - 76))

  const handleClick = (e: React.MouseEvent): void => {
    e.stopPropagation()
    void playVoiceBar(messageId, true)
  }

  return (
    <button
      aria-label={isPlaying ? dict.chat.voice.stop : dict.chat.voice.play}
      className={cn(
        'group/voicebar relative inline-flex items-center justify-between rounded-2xl px-3.5 py-2 text-xs backdrop-blur-md transition select-none cursor-pointer',
        'border border-line-standard bg-surface-card text-strong shadow-xs hover:border-line-strong hover:bg-surface-card/90',
        isPlaying && 'bg-accent-soft/40 border-accent-line/50'
      )}
      onClick={handleClick}
      style={{ width: `${widthPx}px` }}
      type="button"
    >
      <div className="flex items-center gap-1.5">
        {isLoading ? (
          <Loader2 className="size-3.5 shrink-0 text-accent animate-spin" />
        ) : isPlaying ? (
          <Volume2 className="size-3.5 shrink-0 text-accent animate-pulse" />
        ) : (
          <Volume2 className="size-3.5 shrink-0 text-muted group-hover/voicebar:text-strong transition-colors" />
        )}
      </div>
      <span
        className={cn(
          'ml-2 text-[11px] font-medium tracking-tight',
          isPlaying ? 'text-accent font-semibold' : 'text-muted'
        )}
      >
        {hasRealDuration ? `${duration}″` : '…'}
      </span>
    </button>
  )
}

interface TranscriptBlockProps {
  text: string
}

export function TranscriptBlock({ text }: TranscriptBlockProps): React.JSX.Element | null {
  const dict = useStrings()
  const [expanded, setExpanded] = useState(false)
  const trimmed = text.trim()

  if (!trimmed) {
    return null
  }

  return (
    <div className="mt-1 flex max-w-full flex-col select-none">
      <button
        aria-expanded={expanded}
        className={cn(
          'group/transcript inline-flex items-center gap-1 rounded-md border border-line-standard bg-surface-card px-2 py-0.5 text-[10px] text-muted backdrop-blur-xs transition-all duration-150',
          'hover:border-line-strong hover:bg-surface-card/90 hover:text-strong cursor-pointer text-left shadow-xs'
        )}
        onClick={() => setExpanded(prev => !prev)}
        type="button"
      >
        <FileText className="size-2.5 shrink-0 text-muted group-hover/transcript:text-strong transition-colors" />
        <span className="font-normal text-muted group-hover/transcript:text-strong transition-colors">
          {expanded ? dict.chat.voice.collapse : dict.chat.voice.showTranscript}
        </span>
        <ChevronDown
          className={cn(
            'size-2.5 shrink-0 text-muted transition-transform duration-150 group-hover/transcript:text-strong',
            expanded ? 'rotate-180' : '-rotate-90'
          )}
        />
      </button>
      {expanded ? (
        <div className="mt-1 max-h-60 max-w-full overflow-y-auto rounded-lg border border-line-standard bg-surface-card p-2.5 text-xs leading-relaxed text-strong shadow-inner backdrop-blur-md select-text cursor-text whitespace-pre-wrap break-words font-sans">
          {trimmed}
        </div>
      ) : null}
    </div>
  )
}
