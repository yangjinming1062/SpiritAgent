import { atom } from 'nanostores'

export type LifeFrameStatus = 'started' | 'finished' | 'failed' | 'interrupted'

export interface LifeStageMedia {
  key: string
  owner: string
  kind: 'image' | 'video'
  url: string
  loop: boolean
  natural: boolean
  mediaKey: string
  sceneHash: string | null
  presentationEpoch: number
  expiresAt: number | null
  playback: (status: LifeFrameStatus, error?: string) => void
  cleared: (isEmpty: () => boolean) => void
}

/** 主窗口的完整画面及其播放反馈。 */
export const $lifeStageMedia = atom<LifeStageMedia | null>(null)
