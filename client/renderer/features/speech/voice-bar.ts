import { log } from '@/shared/lib/log'
import { registerStorageClearHandler } from '@/shared/lib/storage'
import { backendApi } from '@/shared/platform/desktop'
import { presentationPorts } from '@/shared/presentation-ports'
import { $auth } from '@/shared/store/auth'
import { $whisperOpen } from '@/shared/store/chat-visibility'
import { $presentation } from '@/shared/store/presentation'
import {
  $surfaceOpen,
  $surfaceOpenVisible,
  $surfaceRole,
  $surfaceScreenLocked,
  $surfaceSpriteVisible,
  isSpriteStageShown
} from '@/shared/store/surfaces'
import { voicePlaybackKey, type VoicePlaybackRecord, type VoicePlaybackScope } from '@ipc/contracts'
import type { ReplyAudio } from '@protocol'

import { type AudioPlaybackOptions, playDataUrl, stopAudio } from './audio-track'
import { $callAudioBusy, bindCallAudioFocus } from './call-audio-focus'

export interface VoiceBarTarget extends VoicePlaybackScope {
  messageId: string
  bubbleIndex: number
}

export interface VoiceBarProjection {
  isActive(sessionId: string): boolean
  getVisibleSessionIds(): string[] | undefined
  getSelectedSessionId(): string | null
  getAudio(messageId: string): ReplyAudio | null | undefined
  getRecord(messageId: string): VoicePlaybackRecord | undefined
  getTarget(messageId: string): VoiceBarTarget | null
  resolveId(target: VoiceBarTarget): string | undefined
  getFollowingTargets(messageId: string): VoiceBarTarget[]
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
let queue: VoiceBarTarget[] = []
const received = new Set<string>()
let autoplayAuthSessionId: string | null = null
let autoplayEligibleSince = new Map<string, number>()

function targetKey({ authSessionId, sessionId, messageId, bubbleIndex }: VoiceBarTarget): string {
  return `${authSessionId}:${sessionId}:${voicePlaybackKey(messageId, bubbleIndex)}`
}

export function bindVoiceBarProjection(value: VoiceBarProjection): void {
  projection = value
}

function voiceProjection(): VoiceBarProjection {
  if (!projection) {
    throw new Error('voice bar projection not bound')
  }

  return projection
}

function visibleVoiceSessions(): string[] {
  if (
    $callAudioBusy.get() ||
    recording ||
    $auth.get().kind !== 'authenticated' ||
    $surfaceScreenLocked.get() ||
    presentationPorts().$screenLocked.get()
  ) {
    return []
  }

  const scoped = projection?.getVisibleSessionIds()

  if (scoped) {
    return scoped
  }

  const selected = projection?.getSelectedSessionId()

  if (!mounted || !selected) {
    return []
  }

  const role = $surfaceRole.get()

  const visible =
    role === 'main'
      ? $surfaceOpen.get() && $surfaceOpenVisible.get()
      : role === 'sprite' && $whisperOpen.get() && isSpriteStageShown()

  return visible ? [selected] : []
}

function refreshAutoplayEligibility(visible = visibleVoiceSessions()): void {
  const auth = $auth.get()
  const authSessionId = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null

  if (authSessionId !== autoplayAuthSessionId) {
    autoplayEligibleSince.clear()
    autoplayAuthSessionId = authSessionId
  }

  const now = Date.now()
  autoplayEligibleSince = new Map(
    projection?.isAutoplay() ? visible.map(id => [id, autoplayEligibleSince.get(id) ?? now]) : []
  )
}

export function refreshVoiceBarVisibility(): void {
  const visible = visibleVoiceSessions()
  refreshAutoplayEligibility(visible)

  const id = activeId ?? pausedId
  const sessionId = id ? projection?.getTarget(id)?.sessionId : queue[0]?.sessionId

  if ((id || sessionId) && (!sessionId || !visible.includes(sessionId))) {
    cancelVoiceBar()
  }
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
  refreshAutoplayEligibility()

  if (!value) {
    cancelVoiceBar()
  }
}

export function setVoiceRecording(value: boolean): void {
  recording = value
  refreshAutoplayEligibility()

  if (value) {
    cancelVoiceBar()
  }
}

export function refreshVoiceAutoplay(): void {
  refreshAutoplayEligibility()

  if (!voiceProjection().isAutoplay()) {
    queue = []
  }
}

function playNext(): void {
  const proj = voiceProjection()

  if (activeId || pausedId || !proj.isAutoplay()) {
    return
  }

  for (let next = queue.shift(); next; next = queue.shift()) {
    const id = proj.resolveId(next)

    if (id && visibleVoiceSessions().includes(next.sessionId)) {
      void playItem(id, next, false)

      return
    }
  }
}

async function playItem(messageId: string, target: VoiceBarTarget, manual: boolean, fromStart = false): Promise<void> {
  const proj = voiceProjection()
  const token = ++playToken

  const valid = (): boolean =>
    token === playToken && proj.resolveId(target) === messageId && visibleVoiceSessions().includes(target.sessionId)

  activeId = messageId
  pausedId = null
  proj.setPaused(null)
  proj.setLoading(messageId)

  try {
    await proj.ready(messageId)

    if (!valid() || (!manual && proj.getRecord(messageId)?.listened)) {
      return
    }

    const onProgress = proj.captureProgress(messageId)

    if (fromStart) {
      onProgress(0, false, true)
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
    const dataUrl = await backendApi().apiAsset({ url: audio.url, preferCache: true })

    if (!valid()) {
      return
    }

    if (!dataUrl) {
      throw new Error('Voice audio is unavailable')
    }

    const result = await playDataUrl(dataUrl, {
      startAtSeconds: fromStart ? 0 : (proj.getRecord(messageId)?.positionSeconds ?? 0),
      onProgress,
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

export function enqueueVoiceBars(messageIds: string[], receivedAt?: number | null): void {
  const proj = voiceProjection()
  const visible = visibleVoiceSessions()

  for (const id of messageIds) {
    const target = proj.getTarget(id)

    if (!target) {
      continue
    }

    const key = targetKey(target)

    if (received.has(key)) {
      continue
    }

    received.add(key)
    const eligibleSince = autoplayEligibleSince.get(target.sessionId)

    if (
      receivedAt !== null &&
      (receivedAt === undefined || (eligibleSince !== undefined && receivedAt > eligibleSince)) &&
      visible.includes(target.sessionId) &&
      proj.isAutoplay() &&
      !proj.getRecord(id)?.listened &&
      !queue.some(item => targetKey(item) === key) &&
      activeId !== id
    ) {
      queue.push(target)
    }
  }

  playNext()
}

function queueFollowing(messageId: string): void {
  const proj = voiceProjection()

  queue = proj.isAutoplay() ? proj.getFollowingTargets(messageId) : []
}

export function toggleVoiceBar(messageId: string): void {
  const proj = voiceProjection()
  const target = proj.getTarget(messageId)

  if (!target || !visibleVoiceSessions().includes(target.sessionId) || !proj.isActive(target.sessionId)) {
    return
  }

  if (activeId === messageId) {
    stopCurrent()
    pausedId = messageId
    proj.setPaused(messageId)

    return
  }

  const resuming = pausedId === messageId
  stopCurrent()

  if (!resuming) {
    queueFollowing(messageId)
  }

  void playItem(messageId, target, true)
}

export function restartVoiceBar(messageId: string): void {
  const proj = voiceProjection()
  const target = proj.getTarget(messageId)

  if (!target || !visibleVoiceSessions().includes(target.sessionId) || !proj.isActive(target.sessionId)) {
    return
  }

  stopCurrent()
  queueFollowing(messageId)
  void playItem(messageId, target, true, true)
}

export function bindVoiceBarListeners(): () => void {
  const disposers = [
    bindCallAudioFocus(),
    $callAudioBusy.listen(refreshVoiceBarVisibility),
    presentationPorts().$screenLocked.listen(refreshVoiceBarVisibility),
    $surfaceScreenLocked.listen(refreshVoiceBarVisibility),
    $surfaceOpen.listen(refreshVoiceBarVisibility),
    $surfaceOpenVisible.listen(refreshVoiceBarVisibility),
    $surfaceRole.listen(refreshVoiceBarVisibility),
    $surfaceSpriteVisible.listen(refreshVoiceBarVisibility),
    $presentation.listen(refreshVoiceBarVisibility),
    $whisperOpen.listen(refreshVoiceBarVisibility),
    $auth.listen(refreshVoiceBarVisibility)
  ]

  refreshAutoplayEligibility()

  window.addEventListener('beforeunload', cancelVoiceBar)

  return () => {
    disposers.forEach(dispose => dispose())
    window.removeEventListener('beforeunload', cancelVoiceBar)
    cancelVoiceBar()
    autoplayEligibleSince.clear()
  }
}

registerStorageClearHandler(() => {
  cancelVoiceBar()
  received.clear()
  autoplayEligibleSince.clear()
  recording = false
})
