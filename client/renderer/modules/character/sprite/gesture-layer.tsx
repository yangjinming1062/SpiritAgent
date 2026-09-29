import { useStore } from '@nanostores/react'
import { type RefObject, useEffect } from 'react'

import { $spatialPos, $spatialScale, $spriteContentRect, getBaseSpriteHeight, getBaseSpriteWidth } from '../spatial'

import { $spriteGesture, SPRITE_GESTURE_MS, type SpriteGesture } from './gesture'

type BodyGesture = Extract<SpriteGesture, { kind: 'land' | 'catch' | 'squeeze' }>

// 形变以内容脚底为原点：落地压扁回弹、接取下沉承接、长按轻挤。
const BODY_KEYFRAMES: Record<BodyGesture['kind'], Keyframe[]> = {
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

const FULL_BOX = { bottom: 1, left: 0, right: 1, top: 0 } as const

function isBodyGesture(gesture: SpriteGesture): gesture is BodyGesture {
  return gesture.kind === 'land' || gesture.kind === 'catch' || gesture.kind === 'squeeze'
}

/** 在形象层播放整体形变；降低动态偏好时跳过，新形变从头播放。 */
export function useSpriteBodyGesture(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    let running: Animation | null = null

    const unlisten = $spriteGesture.listen(gesture => {
      if (!gesture || !isBodyGesture(gesture) || reducedMotion.matches) {
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

/** 仪式指向与点击提示：在视口坐标中从精灵朝目标一侧引出指向线并标出目标，不参与命中。 */
export function SpriteTargetCue({ hidden }: { hidden: boolean }): React.JSX.Element | null {
  const gesture = useStore($spriteGesture)
  const pos = useStore($spatialPos)
  const scale = useStore($spatialScale)
  const content = useStore($spriteContentRect) ?? FULL_BOX

  if (hidden || !gesture || isBodyGesture(gesture)) {
    return null
  }

  const target = gesture.target
  const width = getBaseSpriteWidth() * scale
  const height = getBaseSpriteHeight() * scale
  const towardRight = target.x >= pos.x + ((content.left + content.right) / 2) * width
  const handX = pos.x + (towardRight ? content.right : content.left) * width
  const handY = pos.y + (content.top + (content.bottom - content.top) * 0.4) * height

  return (
    <svg aria-hidden="true" className="sprite-target-cue" data-kind={gesture.kind} key={gesture.seq}>
      {gesture.kind === 'point' ? (
        <line className="sprite-target-cue__beam" x1={handX} x2={target.x} y1={handY} y2={target.y} />
      ) : null}
      <circle className="sprite-target-cue__ring" cx={target.x} cy={target.y} r={14} />
    </svg>
  )
}
