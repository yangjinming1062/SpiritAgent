import type { Atom } from 'nanostores'

import { createPort } from '@/shared/lib/port'

import type { ChatMediaItem } from './types/spiritagent'

// 模块间的窄能力端口：各入口渲染前经 app/bootstrap/bind-presentation.ts 绑定角色、语音与媒体实现，conversation、speech 等模块只经本端口访问。详见 client/renderer/README.md「装配与状态归属」。

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
  durationMs?: number
  force?: boolean
}

interface PresentationPorts {
  $activeAvatarId: Atom<number | null>
  $companionVoiceId: Atom<string>
  $portraitUrl: Atom<string | null>
  $screenLocked: Atom<boolean>
  getResponsePreference: () => 'text' | 'voice'
  openMediaViewer: (item: ChatMediaItem, ownerViewId?: string) => void
  setSpriteState: (name: SpriteStateName, options?: SetSpriteStateOptions) => void
  /** 预制台词的合成+播放（内容寻址落盘缓存）：角色反应池经此送达，避免模块直连语音引擎。 */
  speakScripted: (text: string, voice?: string, context?: string) => Promise<boolean>
}

const port = createPort<PresentationPorts>('presentation ports')

export const bindPresentationPorts = port.bind
export const presentationPorts = port.get
