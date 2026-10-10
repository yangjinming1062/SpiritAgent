import { type Atom, atom, map } from 'nanostores'

import { createPort } from '@/shared/lib/port'
import { registerStorageClearHandler } from '@/shared/lib/storage'

export const $voiceBarPlayingId = atom<string | null>(null)
export const $voiceBarLoadingId = atom<string | null>(null)
export const $voiceBarPausedId = atom<string | null>(null)
export const $voiceBarFailedIds = map<Record<string, boolean>>({})

registerStorageClearHandler(() => {
  $voiceBarPlayingId.set(null)
  $voiceBarLoadingId.set(null)
  $voiceBarPausedId.set(null)
  $voiceBarFailedIds.set({})
})

export function setVoiceBarPlaying(id: string | null): void {
  $voiceBarPlayingId.set(id)
}

export function setVoiceBarLoading(id: string | null): void {
  $voiceBarLoadingId.set(id)
}

export function setVoiceBarFailed(id: string, failed: boolean): void {
  $voiceBarFailedIds.setKey(id, failed ? true : undefined)
}

export function setVoiceBarPaused(id: string | null): void {
  $voiceBarPausedId.set(id)
}

export function activeVoiceMessageId(): string | null {
  return $voiceBarPlayingId.get() ?? $voiceBarLoadingId.get() ?? $voiceBarPausedId.get()
}

export interface ConversationVoiceSink {
  cancel(sessionId?: string | null, reason?: 'selection'): void
  enqueue(messageIds: string[]): void
  setVisible(visible: boolean): void
  setRecording(recording: boolean): void
}

const voiceSinkPort = createPort<ConversationVoiceSink>('conversation voice sink')

export const setConversationVoiceSink = voiceSinkPort.bind
export const conversationVoiceSink = voiceSinkPort.get

export interface VoiceBarControl {
  $autoplay: Atom<boolean>
  restart(messageId: string): void
  setAutoplay(value: boolean): void
  toggle(messageId: string): void
}

const voiceBarControlPort = createPort<VoiceBarControl>('voice bar control')

export const setVoiceBarControl = voiceBarControlPort.bind
export const voiceBarControl = voiceBarControlPort.get
