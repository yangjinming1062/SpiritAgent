import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'

/** 落地、接取、长按的整体形变；seq 区分同类反馈的每次触发。 */
export interface SpriteGesture {
  readonly kind: 'land' | 'catch' | 'squeeze'
  readonly seq: number
}

export const SPRITE_GESTURE_MS: Record<SpriteGesture['kind'], number> = {
  catch: 520,
  land: 420,
  squeeze: 360
}

export const $spriteGesture = atom<SpriteGesture | null>(null)

let gestureSeq = 0

/** 新反馈替换旧反馈。 */
export function playSpriteGesture(input: Pick<SpriteGesture, 'kind'>): void {
  gestureSeq += 1
  const gesture: SpriteGesture = { ...input, seq: gestureSeq }

  $spriteGesture.set(gesture)
}

export function clearSpriteGesture(): void {
  $spriteGesture.set(null)
}

registerStorageClearHandler(() => clearSpriteGesture())
