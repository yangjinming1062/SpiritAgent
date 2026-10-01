import { useStore } from '@nanostores/react'
import { useLayoutEffect, useRef, useState } from 'react'

import { openSessionSurface, openWhisper } from '@/app/workflows/session-delivery'
import {
  $effectiveTier,
  $screenLocked,
  $spatialPeek,
  $spatialPos,
  $spatialScale,
  $spriteContentRect,
  $spriteHeadRect,
  $viewport,
  computeOverlayAnchorBesideSprite
} from '@/modules/character'
import { $proactiveBubble, pendingMessages, setProactiveBubble } from '@/modules/conversation'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { cn } from '@/shared/lib/utils'
import { $chatVisible } from '@/shared/store/chat-visibility'

// 仅在桌面提示未读；可见期间订阅轮廓与空间状态，点击按所属会话进入对话。
const BUBBLE_GAP = 8
const BUBBLE_MAX_W = 256
const BUBBLE_VERTICAL_RATIO = 0.1

export function ProactiveBubble(): React.JSX.Element | null {
  const transient = useStore($proactiveBubble)
  const pending = useStore(pendingMessages.$atom)
  const tier = useStore($effectiveTier)
  const locked = useStore($screenLocked)
  const state = transient ?? pending.at(-1)
  const chatVisible = useStore($chatVisible)

  if (!state || chatVisible || tier === 'still' || locked) {
    return null
  }

  return <ProactiveBubbleView sessionId={state.sessionId} text={state.text} />
}

function ProactiveBubbleView({ text, sessionId }: { text: string; sessionId?: string }): React.JSX.Element {
  const pos = useStore($spatialPos)
  const scale = useStore($spatialScale)
  const viewport = useStore($viewport)
  const contentRect = useStore($spriteContentRect)
  const headRect = useStore($spriteHeadRect)
  const peek = useStore($spatialPeek)
  const bubbleRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: BUBBLE_MAX_W, height: 0 })

  useInteractiveRegion('proactive-bubble', bubbleRef)

  useLayoutEffect(() => {
    const element = bubbleRef.current

    if (!element) {
      return
    }

    const measure = (): void => {
      const { width, height } = element.getBoundingClientRect()
      setSize(previous => (previous.width === width && previous.height === height ? previous : { width, height }))
    }

    const observer = new ResizeObserver(measure)
    observer.observe(element)
    measure()

    return () => observer.disconnect()
  }, [])

  const { left, top, side } = computeOverlayAnchorBesideSprite({
    pos,
    scale,
    anchorRect: headRect ?? contentRect,
    peek,
    gap: BUBBLE_GAP,
    overlayW: size.width,
    overlayH: size.height,
    vw: viewport.width,
    vh: viewport.height,
    verticalRatio: BUBBLE_VERTICAL_RATIO
  })

  // 工作会话不能送进轻语：带会话的提示按会话归属选入口。
  const handleClick = (): void => {
    if (sessionId) {
      openSessionSurface(sessionId)
    } else {
      openWhisper()
    }

    setProactiveBubble(null)
  }

  return (
    <div
      className="proactive-bubble fixed z-30 w-max cursor-pointer select-none"
      onClick={handleClick}
      ref={bubbleRef}
      style={{ left, top, maxWidth: Math.min(BUBBLE_MAX_W, viewport.width) }}
    >
      <style>{`@keyframes proactiveIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}.proactive-bubble>span{animation:proactiveIn .25s ease-out}`}</style>
      <span
        className={cn(
          'block rounded-2xl',
          side === 'right' ? 'rounded-tl-sm' : 'rounded-tr-sm',
          'border border-line-standard bg-surface-card px-3.5 py-2 text-sm leading-relaxed break-words text-strong shadow-xl backdrop-blur-glass transition hover:bg-fill-hover'
        )}
      >
        {text}
      </span>
    </div>
  )
}
