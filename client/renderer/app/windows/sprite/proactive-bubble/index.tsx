import { useStore } from '@nanostores/react'
import { useRef } from 'react'

import { openSessionSurface } from '@/app/workflows/session-delivery'
import { $effectiveTier, $screenLocked } from '@/modules/character'
import { $spatialPos, $spatialScale, $viewport, computeOverlayAnchorBesideSprite } from '@/modules/character'
import { $proactiveBubble, setProactiveBubble } from '@/modules/conversation'
import { pendingMessages } from '@/modules/conversation'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { $chatVisible } from '@/shared/store/chat-visibility'

import { openWhisper } from '../whisper'

// 伙伴主动消息的临时气泡：对话入口均未打开时显示在伙伴身边（DESIGN「主动陪伴」）；生活空间/工作台/轻语任一打开时消息已在对话流里，不重复显示。富媒体不进气泡，点击按所属会话打开轻语或工作台。锚定在精灵身边跟随拖拽/行走/飞行/聊天场所重新定位；外层在「无消息」时短路，保证 spatial 订阅只在气泡显示期间才跑。
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
