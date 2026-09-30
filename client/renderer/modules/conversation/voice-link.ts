import { atom, map } from 'nanostores'

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

export interface ConversationVoiceSink {
  cancel(): void
  enqueue(messageIds: string[]): void
  setVisible(visible: boolean): void
  setRecording(recording: boolean): void
}
let voiceSink: ConversationVoiceSink | null = null

export function setConversationVoiceSink(sink: ConversationVoiceSink): void {
  voiceSink = sink
}

export function conversationVoiceSink(): ConversationVoiceSink {
  if (!voiceSink) {
    throw new Error('conversation voice sink not bound')
  }

  return voiceSink
}

export interface VoiceBarControl {
  toggle(messageId: string): void
}
let voiceControl: VoiceBarControl | null = null

export function setVoiceBarControl(control: VoiceBarControl): void {
  voiceControl = control
}

export function voiceBarControl(): VoiceBarControl {
  if (!voiceControl) {
    throw new Error('voice bar control not bound')
  }

  return voiceControl
}
