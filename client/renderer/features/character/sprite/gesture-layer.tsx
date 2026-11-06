import { type RefObject, useEffect } from 'react'

import { $spriteGesture, SPRITE_GESTURE_MS, type SpriteGesture } from './gesture'

// 形变以内容脚底为原点：落地压扁回弹、接取下沉承接、长按轻挤。
const BODY_KEYFRAMES: Record<SpriteGesture['kind'], Keyframe[]> = {
  catch: [
    { transform: 'translateY(0) scale(1, 1)' },
    { offset: 0.35, transform: 'translateY(3%) scale(1.03, 0.96)' },
    { offset: 0.7, transform: 'translateY(-2%) scale(0.99, 1.02)' },
    { transform: 'translateY(0) scale(1, 1)' }
  ],
  land: [
    { transform: 'scale(1, 1)' },
    { offset: 0.3, transform: 'scale(1.06, 0.92)' },
    { offset: 0.65, transform: 'scale(0.98, 1.03)' },
    { transform: 'scale(1, 1)' }
  ],
  squeeze: [{ transform: 'scale(1, 1)' }, { offset: 0.4, transform: 'scale(1.04, 0.95)' }, { transform: 'scale(1, 1)' }]
}

/** 在形象层播放整体形变；降低动态偏好时跳过，新形变从头播放。 */
export function useSpriteBodyGesture(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    let running: Animation | null = null

    const unlisten = $spriteGesture.listen(gesture => {
      if (!gesture || reducedMotion.matches) {
        return
      }

      running?.cancel()
      running =
        ref.current?.animate(BODY_KEYFRAMES[gesture.kind], {
          duration: SPRITE_GESTURE_MS[gesture.kind],
          easing: 'ease-out'
        }) ?? null
    })

    return () => {
      unlisten()
      running?.cancel()
    }
  }, [ref])
}
