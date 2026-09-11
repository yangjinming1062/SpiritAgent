import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { $spatialPos, $spatialScale, $viewport, computeOverlayAnchorBesideSprite } from '@/modules/character'
import {
  $chatSessionId,
  $companionSessionId,
  consumePendingMessages,
  ConversationInput,
  ConversationSurface,
  openMainSession,
  pendingMessages,
  useChatInput,
  useIsReadOnlySession
} from '@/modules/conversation'
import { usePointerDrag } from '@/shared/hooks/use-pointer-drag'
import { X } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { $gatewayState } from '@/shared/store/gateway'
import { $surfaceOpen } from '@/shared/store/surfaces'
import { getStrings } from '@/shared/strings'

import { $whisperOffset, $whisperOpen, closeWhisper, setWhisperOffset } from './whisper-store'

const WHISPER_WIDTH = 380
const WHISPER_HEIGHT = 540
const WHISPER_GAP = 12
const HEADER_HEIGHT = 28

// 外层只负责可见性判定；所有 hook 集中在内层，仅在打开时挂载。
export function WhisperOverlay(): React.JSX.Element | null {
  const open = useStore($whisperOpen)
  const surfaceOpen = useStore($surfaceOpen)

  if (!open || surfaceOpen !== null) {
    return null
  }

  return <WhisperOverlayContent />
}

function WhisperOverlayContent(): React.JSX.Element {
  const sessionId = useStore($chatSessionId)
  const companionSessionId = useStore($companionSessionId)
  const gatewayState = useStore($gatewayState)
  const pos = useStore($spatialPos)
  const scale = useStore($spatialScale)
  const viewport = useStore($viewport)
  const pending = useStore(pendingMessages.$atom)
  const baseOffset = useStore($whisperOffset) ?? { dx: 0, dy: 0 }

  const containerRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const isReadOnlySession = useIsReadOnlySession()
  const input = useChatInput({ gatewayState, isReadOnlySession })

  useInteractiveRegion('whisper-overlay', containerRef)

  // 轻语和生活空间一样加载唯一的陪伴会话——未加载或当前不在陪伴会话时定位到主陪伴会话。
  useEffect(() => {
    if (gatewayState !== 'open') {
      return
    }

    if (!companionSessionId || sessionId !== companionSessionId) {
      void openMainSession()
    }
  }, [gatewayState, companionSessionId, sessionId])

  // ConversationSurface 的 pending 消费依赖 surfaceOpen === surfaceRole，轻语不是 surface，
  // 必须自己在打开期间持续消费，否则已读消息关掉后气泡会再弹。
  useEffect(() => {
    if (sessionId && pending.some(item => item.sessionId === sessionId)) {
      consumePendingMessages(sessionId)
    }
  }, [sessionId, pending])

  const anchor = computeOverlayAnchorBesideSprite({
    gap: WHISPER_GAP,
    overlayH: WHISPER_HEIGHT,
    overlayMaxW: WHISPER_WIDTH,
    pos,
    scale,
    verticalRatio: 0.05,
    vh: viewport.height,
    vw: viewport.width
  })

  // baseOffset 持久化在 atom，松手时把累计 delta 加进去；新 baseOffset 用于下一次渲染。
  const { delta, onPointerDown: onHeaderPointerDown } = usePointerDrag({
    onCommit: ({ dx, dy }) => {
      setWhisperOffset({ dx: baseOffset.dx + dx, dy: baseOffset.dy + dy })
    }
  })

  const translateX = baseOffset.dx + delta.dx
  const translateY = baseOffset.dy + delta.dy

  return (
    <div
      className="fixed z-40 flex flex-col overflow-hidden rounded-2xl border border-line-hairline bg-surface-card/85 shadow-2xl backdrop-blur-glass backdrop-saturate-180"
      onDragOver={e => {
        e.preventDefault()
      }}
      onDrop={input.handleDrop}
      ref={containerRef}
      style={{
        height: `${WHISPER_HEIGHT}px`,
        left: `${anchor.left}px`,
        pointerEvents: 'auto',
        top: `${anchor.top}px`,
        transform: `translate3d(${translateX}px, ${translateY}px, 0)`,
        width: `${WHISPER_WIDTH}px`
      }}
    >
      <div
        className="flex shrink-0 cursor-move items-center justify-end px-2"
        onPointerDown={onHeaderPointerDown}
        style={{ height: `${HEADER_HEIGHT}px` }}
      >
        <button
          aria-label={getStrings().whisper.close}
          className="flex size-5 items-center justify-center rounded text-faint transition hover:bg-fill-hover hover:text-strong"
          onClick={closeWhisper}
          onPointerDown={e => e.stopPropagation()}
          type="button"
        >
          <X className="size-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        <ConversationSurface scrollRef={scrollRef} variant="living" />
      </div>
      <div className="shrink-0">
        <ConversationInput {...input.inputProps} variant="living" />
      </div>
    </div>
  )
}
