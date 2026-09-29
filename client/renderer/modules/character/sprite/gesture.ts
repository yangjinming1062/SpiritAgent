import { atom } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'

interface ViewportPoint {
  readonly x: number
  readonly y: number
}

/** 容器反馈：落地、接取、长按的整体形变（DESIGN「拖拽与直接交互」），以及仪式行走对目标的指向与点击提示
 * （DESIGN「仪式性行走」）。姿态仍由动作素材呈现，这里不依赖素材。seq 区分同类反馈的每次触发。 */
export type SpriteGesture =
  | { readonly kind: 'land' | 'catch' | 'squeeze'; readonly seq: number }
  | { readonly kind: 'point' | 'tap'; readonly seq: number; readonly target: ViewportPoint }

type SpriteGestureInput =
  | { readonly kind: 'land' | 'catch' | 'squeeze' }
  | { readonly kind: 'point' | 'tap'; readonly target: ViewportPoint }

// 形变时长即动画时长；目标提示到时自动撤下，指向另由仪式行走切换或清除。
export const SPRITE_GESTURE_MS: Record<SpriteGesture['kind'], number> = {
  catch: 520,
  land: 420,
  point: 3000,
  squeeze: 360,
  tap: 600
}

export const $spriteGesture = atom<SpriteGesture | null>(null)

let gestureSeq = 0
let cueTimer: ReturnType<typeof setTimeout> | null = null

function clearCueTimer(): void {
  if (cueTimer) {
    clearTimeout(cueTimer)
    cueTimer = null
  }
}

/** 触发一次反馈并返回其 seq；新反馈替换旧反馈。 */
export function playSpriteGesture(input: SpriteGestureInput): number {
  gestureSeq += 1
  const gesture: SpriteGesture = { ...input, seq: gestureSeq }

  clearCueTimer()
  $spriteGesture.set(gesture)

  if (gesture.kind === 'point' || gesture.kind === 'tap') {
    cueTimer = setTimeout(() => {
      cueTimer = null

      if ($spriteGesture.get()?.seq === gesture.seq) {
        $spriteGesture.set(null)
      }
    }, SPRITE_GESTURE_MS[gesture.kind])
  }

  return gesture.seq
}

/** 撤下反馈：传 seq 时只撤下仍是该次的反馈；不传表示用户打断，撤下当前全部反馈。 */
export function clearSpriteGesture(seq?: number): void {
  if (seq !== undefined && $spriteGesture.get()?.seq !== seq) {
    return
  }

  clearCueTimer()
  $spriteGesture.set(null)
}

registerStorageClearHandler(() => clearSpriteGesture())
