import { useStore } from '@nanostores/react'
import { useRef } from 'react'

import { $chatSessionId, $proactiveBubble, setProactiveBubble, switchSession } from '@/chat'
import { pendingMessages } from '@/chat'
import { $screenLocked } from '@/companion/activity'
import { $effectiveTier } from '@/companion/companion-store'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { $surfaceOpen, requestOpenSurface } from '@/shared/store/surfaces'

import { $spatialPos, $spatialScale, $viewport, computeOverlayAnchorBesideSprite } from '../spatial'

// 伙伴主动消息的临时气泡：生活空间未打开时显示在伙伴身边（DESIGN §6.2）。
// 生活空间打开时，消息已经在对话流里出现，这里不再重复显示。
// 富媒体不进气泡——媒体送达提示也只以文本出现，点击打开生活空间陪伴对话。
//
// 锚定在精灵身边，跟随拖拽 / 行走 / 飞行 / 聊天场所重新定位；
// 外层在「无消息」时短路掉，保证 spatial 订阅只在气泡显示期间才跑。
const BUBBLE_GAP = 8
const BUBBLE_MAX_W = 256
const BUBBLE_VERTICAL_RATIO = 0.1

export function ProactiveBubble(): React.JSX.Element | null {
  const transient = useStore($proactiveBubble)
  const pending = useStore(pendingMessages.$atom)
  const tier = useStore($effectiveTier)
  const locked = useStore($screenLocked)
  const state = transient ?? pending.at(-1)
  const surfaceOpen = useStore($surfaceOpen)

  if (!state || surfaceOpen !== null || tier === 'still' || locked) {
    return null
  }

  return <ProactiveBubbleView sessionId={state.sessionId} text={state.text} />
}

function ProactiveBubbleView({ text, sessionId }: { text: string; sessionId?: string }): React.JSX.Element {
  const pos = useStore($spatialPos)
  const scale = useStore($spatialScale)
  const viewport = useStore($viewport)
  const bubbleRef = useRef<HTMLDivElement>(null)

  useInteractiveRegion('proactive-bubble', bubbleRef)

  const { left, top } = computeOverlayAnchorBesideSprite({
    pos,
    scale,
    gap: BUBBLE_GAP,
    overlayMaxW: BUBBLE_MAX_W,
    vw: viewport.width,
    vh: viewport.height,
    verticalRatio: BUBBLE_VERTICAL_RATIO
  })

  const handleClick = (): void => {
    if (sessionId && sessionId !== $chatSessionId.get()) {
      void switchSession(sessionId)
    }

    void requestOpenSurface('living', { sessionId, view: 'chat' })
    setProactiveBubble(null)
  }

  return (
    <div
      className="proactive-bubble fixed z-30 max-w-[16rem] cursor-pointer select-none"
      onClick={handleClick}
      ref={bubbleRef}
      style={{ left, top }}
    >
      <style>{`@keyframes proactiveIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}.proactive-bubble>span{animation:proactiveIn .25s ease-out}`}</style>
      <span className="block rounded-2xl rounded-br-sm border border-line-standard bg-surface-card px-3.5 py-2 text-sm leading-relaxed text-strong shadow-xl backdrop-blur-glass transition hover:bg-fill-hover">
        {text}
      </span>
    </div>
  )
}
