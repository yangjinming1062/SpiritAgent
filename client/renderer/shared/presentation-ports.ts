import type { SpeechStyle } from '@ipc/contracts'
import type { Atom } from 'nanostores'

import type { ChatMediaItem } from './types/spiritagent'

// chat 不依赖形象层：companion 加载时绑定实现，chat 只经本端口访问。
// 详见 client/README.md §3 与 eslint chat 边界规则。

export type SpriteStateName =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'working'
  | 'emotional'
  | 'interacting'
  | 'disconnected'

export interface SetSpriteStateOptions {
  action?: string | null
  durationMs?: number
  emotion?: string
  force?: boolean
}

export interface PresentationPorts {
  $activeAvatarId: Atom<number | null>
  $companionVoiceId: Atom<string>
  $portraitUrl: Atom<string | null>
  $responseMode: Atom<'text' | 'voice'>
  $screenLocked: Atom<boolean>
  $spriteState: Atom<SpriteStateName>
  $voicePreparing: Atom<boolean>
  openMediaViewer: (item: ChatMediaItem) => void
  playDataUrl: (dataUrl: string, onDone?: () => void) => Promise<boolean>
  requestSynth: (
    text: string,
    voice?: string,
    context?: string,
    persist?: boolean,
    speechStyle?: SpeechStyle
  ) => Promise<string>
  setSpriteState: (name: SpriteStateName, options?: SetSpriteStateOptions) => void
  speakChatMessage: (text: string, voice?: string, onDone?: () => void, speechStyle?: SpeechStyle) => Promise<boolean>
  stopAudio: () => void
  stopSpeaking: () => void
}

let ports: PresentationPorts | null = null

export function bindPresentationPorts(next: PresentationPorts): void {
  ports = next
}

export function presentationPorts(): PresentationPorts {
  if (!ports) {
    throw new Error('presentation ports not bound — companion must initialize first')
  }

  return ports
}
