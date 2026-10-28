import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'

/** app 将统一舞台计划投影到此端口；character 不依赖成品库。 */
export interface WindowActorMedia {
  key: string
  owner: string
  url: string
  width: number
  height: number
  loop: boolean
  baseKey: string | null
  body: {
    footAnchor: [number, number] | null
    headAnchor: [number, number] | null
    bodyBounds: [number, number, number, number] | null
    hitBounds: [number, number, number, number] | null
  } | null
}

export const $windowActorMedia = atom<WindowActorMedia | null>(null)
export const $windowActorPlayback = atom<{
  key: string
  status: 'started' | 'finished' | 'failed'
  error?: string
} | null>(null)

registerStorageClearHandler(() => {
  $windowActorMedia.set(null)
  $windowActorPlayback.set(null)
})
