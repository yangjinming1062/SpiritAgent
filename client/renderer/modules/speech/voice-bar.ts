import { registerStorageClearHandler } from '@/shared/lib/storage'
import { presentationPorts } from '@/shared/presentation-ports'
import { $whisperOpen } from '@/shared/store/chat-visibility'
import { $surfaceOpen, isLivingProxyWindow } from '@/shared/store/surfaces'
import type { ReplyAudio } from '@/shared/types/spiritagent'

// 聊天语音仅播放服务端保存的音频，不从文字合成，不受回应偏好影响。
export interface VoiceBarProjection {
  getAudio(messageId: string): ReplyAudio | null | undefined
  retry(messageId: string): Promise<void>
  setPlaying(messageId: string | null): void
  setLoading(messageId: string | null): void
  setFailed(messageId: string | null): void
  getPlaying(): string | null
  getLoading(): string | null
}
let projection: VoiceBarProjection | null = null
let playToken = 0

export function bindVoiceBarProjection(value: VoiceBarProjection): void {
  projection = value
}

function voiceProjection(): VoiceBarProjection {
  if (!projection) {
    throw new Error('voice bar projection not bound')
  }

  return projection
}

function isVoiceSurfaceVisible(): boolean {
  if (presentationPorts().$screenLocked.get()) {
    return false
  }

  return isLivingProxyWindow() ? $surfaceOpen.get() === 'living' : $whisperOpen.get() && $surfaceOpen.get() === null
}

export function cancelVoiceBar(): void {
  playToken++
  const proj = voiceProjection()
  proj.setPlaying(null)
  proj.setLoading(null)
  proj.setFailed(null)
  // 同一音频通道还承载直接互动；停止也须作废尚未完成的合成。
  presentationPorts().stopAudio()
}

export async function toggleVoiceBar(messageId: string): Promise<void> {
  if (!isVoiceSurfaceVisible()) {
    return
  }

  const proj = voiceProjection()
  const wasActive = proj.getPlaying() === messageId || proj.getLoading() === messageId
  cancelVoiceBar()

  if (wasActive) {
    return
  }

  const token = playToken
  proj.setLoading(messageId)

  try {
    if (!proj.getAudio(messageId)) {
      await proj.retry(messageId)
    }

    if (token !== playToken || !isVoiceSurfaceVisible()) {
      return
    }

    const audio = proj.getAudio(messageId)

    if (!audio) {
      throw new Error('Voice audio is unavailable')
    }

    const dataUrl = await window.spiritagent.apiAsset({ url: audio.url, preferCache: true })

    if (token !== playToken || proj.getAudio(messageId)?.url !== audio.url || !isVoiceSurfaceVisible()) {
      return
    }

    proj.setLoading(null)
    proj.setPlaying(messageId)

    const result = await presentationPorts().playDataUrl(dataUrl, () => {
      if (token === playToken) {
        proj.setPlaying(null)
      }
    })

    if (result === 'failed' && token === playToken) {
      proj.setFailed(messageId)
    }
  } catch (err) {
    if (token === playToken) {
      proj.setFailed(messageId)
      proj.setPlaying(null)
    }

    console.warn('[voice-bar] playback failed', err)
  } finally {
    if (token === playToken) {
      proj.setLoading(null)
    }
  }
}

export function bindVoiceBarListeners(): void {
  const cancelIfHidden = (): void => {
    if (!isVoiceSurfaceVisible()) {
      cancelVoiceBar()
    }
  }

  presentationPorts().$screenLocked.listen(cancelIfHidden)
  $surfaceOpen.listen(cancelIfHidden)
  $whisperOpen.listen(cancelIfHidden)
}

registerStorageClearHandler(cancelVoiceBar)
