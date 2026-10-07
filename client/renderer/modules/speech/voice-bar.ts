import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { $auth } from '@/shared/store/auth'
import { $whisperOpen } from '@/shared/store/chat-visibility'
import {
  $surfaceOpen,
  $surfaceOpenVisible,
  $surfaceRole,
  $surfaceSpriteVisible,
  isSpriteStageShown
} from '@/shared/store/surfaces'
import type { VoicePlaybackRecord } from '@ipc/contracts'
import type { ReplyAudio } from '@protocol'

import { type AudioPlaybackOptions, playDataUrl, stopAudio } from './audio-track'

export interface VoiceBarProjection {
  isVisible?(messageId?: string): boolean | undefined
  getAudio(messageId: string): ReplyAudio | null | undefined
  getRecord(messageId: string): VoicePlaybackRecord | undefined
  getKey(messageId: string): string | null
  getFollowing(messageId: string): string[]
  ready(messageId: string): Promise<void>
  captureProgress(messageId: string): NonNullable<AudioPlaybackOptions['onProgress']>
  isAutoplay(): boolean
  retry(messageId: string): Promise<void>
  setPlaying(messageId: string | null): void
  setLoading(messageId: string | null): void
  setPaused(messageId: string | null): void
  setFailed(messageId: string, failed: boolean): void
}

let projection: VoiceBarProjection | null = null
let playToken = 0
let activeId: string | null = null
let pausedId: string | null = null
let mounted = false
let recording = false
let queue: string[] = []
const received = new Set<string>()

export function bindVoiceBarProjection(value: VoiceBarProjection): void {
  projection = value
}

function voiceProjection(): VoiceBarProjection {
  if (!projection) {
    throw new Error('voice bar projection not bound')
  }

  return projection
}

function isVoiceSurfaceVisible(messageId?: string): boolean {
  if (recording || $auth.get().kind !== 'authenticated' || presentationPorts().$screenLocked.get()) {
    return false
  }

  const scopedVisible = projection?.isVisible?.(messageId)

  if (scopedVisible !== undefined) {
    return scopedVisible
  }

  if (!mounted) {
    return false
  }

  if ($surfaceRole.get() === 'living') {
    return $surfaceOpen.get() === 'living' && $surfaceOpenVisible.get()
  }

  return $surfaceRole.get() === 'sprite' && $whisperOpen.get() && isSpriteStageShown()
}

function stopCurrent(): void {
  // stopAudio 同步保存最后进度；之后再作废下载与异步播放收尾。
  stopAudio()
  playToken++
  activeId = null
  projection?.setPlaying(null)
  projection?.setLoading(null)
}

export function cancelVoiceBar(): void {
  stopCurrent()
  queue = []
  pausedId = null
  projection?.setPaused(null)
}

export function setVoiceSurfaceMounted(value: boolean): void {
  mounted = value

  if (!value) {
    cancelVoiceBar()
  }
}

export function setVoiceRecording(value: boolean): void {
  recording = value

  if (value) {
    cancelVoiceBar()
  }
}

export function refreshVoiceAutoplay(): void {
  if (!voiceProjection().isAutoplay()) {
    queue = []
  }
}

function playNext(): void {
  const proj = voiceProjection()

  if (activeId || pausedId || !isVoiceSurfaceVisible() || !proj.isAutoplay()) {
    return
  }

  const next = queue.shift()

  if (next) {
    void playItem(next, false)
  }
}

async function playItem(messageId: string, manual: boolean): Promise<void> {
  const proj = voiceProjection()
  const token = ++playToken
  const key = proj.getKey(messageId)

  const valid = (): boolean =>
    token === playToken && key !== null && proj.getKey(messageId) === key && isVoiceSurfaceVisible(messageId)

  activeId = messageId
  pausedId = null
  proj.setPaused(null)
  proj.setLoading(messageId)

  try {
    await proj.ready(messageId)

    if (!valid() || (!manual && proj.getRecord(messageId)?.listened)) {
      return
    }

    if (!proj.getAudio(messageId) && manual) {
      await proj.retry(messageId)
    }

    if (!valid()) {
      return
    }

    const audio = proj.getAudio(messageId)

    if (!audio) {
      proj.setFailed(messageId, true)

      return
    }

    proj.setFailed(messageId, false)
    const dataUrl = await window.spiritagent.apiAsset({ url: audio.url, preferCache: true })

    if (!valid()) {
      return
    }

    if (!dataUrl) {
      throw new Error('Voice audio is unavailable')
    }

    const result = await playDataUrl(dataUrl, {
      startAtSeconds: proj.getRecord(messageId)?.positionSeconds ?? 0,
      onProgress: proj.captureProgress(messageId),
      onStarted: () => {
        if (valid()) {
          proj.setLoading(null)
          proj.setPlaying(messageId)
        }
      }
    })

    if (token !== playToken) {
      return
    }

    if (result === 'failed') {
      proj.setFailed(messageId, true)
    } else if (result === 'interrupted') {
      pausedId = messageId
      proj.setPaused(messageId)
    }
  } catch (error) {
    if (token === playToken) {
      proj.setFailed(messageId, true)
    }

    log.warn('voice-bar', 'Playback failed', error)
  } finally {
    if (token === playToken) {
      activeId = null
      proj.setLoading(null)
      proj.setPlaying(null)
      playNext()
    }
  }
}

export function enqueueVoiceBars(messageIds: string[]): void {
  const proj = voiceProjection()

  for (const id of messageIds) {
    const key = proj.getKey(id)

    if (!key || received.has(key)) {
      continue
    }

    received.add(key)

    if (
      isVoiceSurfaceVisible(id) &&
      proj.isAutoplay() &&
      !proj.getRecord(id)?.listened &&
      !queue.includes(id) &&
      activeId !== id
    ) {
      queue.push(id)
    }
  }

  playNext()
}

export function toggleVoiceBar(messageId: string): void {
  if (!isVoiceSurfaceVisible(messageId)) {
    return
  }

  const proj = voiceProjection()

  if (activeId === messageId) {
    stopCurrent()
    pausedId = messageId
    proj.setPaused(messageId)

    return
  }

  const resuming = pausedId === messageId
  stopCurrent()

  if (!resuming) {
    queue = proj.isAutoplay() ? proj.getFollowing(messageId) : []
  }

  void playItem(messageId, true)
}

export function bindVoiceBarListeners(): () => void {
  const cancelIfHidden = (): void => {
    if (!isVoiceSurfaceVisible()) {
      cancelVoiceBar()
    }
  }

  const disposers = [
    presentationPorts().$screenLocked.listen(cancelIfHidden),
    $surfaceOpen.listen(cancelIfHidden),
    $surfaceOpenVisible.listen(cancelIfHidden),
    $surfaceSpriteVisible.listen(cancelIfHidden),
    $whisperOpen.listen(cancelIfHidden),
    $auth.listen(cancelIfHidden)
  ]

  window.addEventListener('beforeunload', cancelVoiceBar)

  return () => {
    disposers.forEach(dispose => dispose())
    window.removeEventListener('beforeunload', cancelVoiceBar)
    cancelVoiceBar()
  }
}

registerStorageClearHandler(() => {
  cancelVoiceBar()
  received.clear()
  recording = false
})
