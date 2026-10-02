import { useStore } from '@nanostores/react'
import { type PointerEvent, type ReactNode, useCallback, useEffect, useLayoutEffect, useRef } from 'react'

import { openWhisper } from '@/app/workflows/session-delivery'
import {
  $expressionBoost,
  $homePosition,
  $spatialLocomotion,
  $spatialPeek,
  $spatialPos,
  $spatialScale,
  $spriteContentRect,
  cancelMovement,
  emitVfx,
  endDragAt,
  FootGlow,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  handleDragEndInteraction,
  peekMaskRects,
  playSpriteGesture,
  setSpriteState,
  SpriteTargetCue,
  SpriteVfxOverlay,
  startDrag,
  updateDragPosition,
  useSpriteBodyGesture
} from '@/modules/character'
import { useVideoPixelHitTest } from '@/modules/character/rendering/video'
import { clearExternalAttachment, pushExternalAttachment } from '@/modules/conversation'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import { holdWindowMouseCapture, useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { log } from '@/shared/lib/log'
import { notifyError } from '@/shared/store/notifications'
import { $surfaceOpen, requestOpenSurface } from '@/shared/store/surfaces'
import { getStrings } from '@/shared/strings'

interface SpriteStageProps {
  children: ReactNode
  onTap?: () => void
  onDoubleTap?: () => void
  onContextMenu?: (e: React.MouseEvent) => void
  hidden?: boolean
  windowId?: number
  allowDisplaySwitch?: boolean
  onDropFiles?: (files: FileList) => void
  domHitPassthrough?: boolean
}

// 12px 是为了避免触控板微抖动被误判为拖拽、把双击吞掉。
const DRAG_THRESHOLD = 12
const DOUBLE_TAP_MS = 320
// 长按 ≥500ms 触发形变与粒子；拖拽一旦启动即取消等待，两条交互通道互斥。
const LONG_PRESS_MS = 500
// 投喂分流：纯图片/视频走轻语快速回复；混有其它文件时整批进生活空间。
const MEDIA_DROP_PATH_RE = /\.(png|jpe?g|gif|webp|bmp|svg|avif|mp4|mov|webm|m4v|avi|mkv)$/i

// 跨显示器后 pointer capture 会投递跨视口坐标，按此间隔探测主进程。
const DISPLAY_SWITCH_PROBE_MS = 200

export const SPRITE_REGION_ID = 'sprite-stage'

export function SpriteStage({
  children,
  onTap,
  onDoubleTap,
  onContextMenu,
  hidden = false,
  windowId = 0,
  allowDisplaySwitch = true,
  onDropFiles,
  domHitPassthrough = false
}: SpriteStageProps): React.JSX.Element {
  const mountRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)

  const dragRef = useRef<{
    startX: number
    startY: number
    originX: number
    originY: number
    moved: boolean
    lastX: number
    lastY: number
    pointerId: number
    target: HTMLDivElement
    releaseCapture: () => void
    longPressed: boolean
  } | null>(null)

  const gestureGenerationRef = useRef(0)
  const displayProbePendingRef = useRef(false)

  const longPressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 单击延迟执行，给双击让路：避免双击首击误触发单击动作。
  const tapTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const lastTapRef = useRef(0)
  const pos = useStore($spatialPos)
  const scale = useStore($spatialScale)
  const peek = useStore($spatialPeek)
  const expressionBoost = useStore($expressionBoost)
  const content = useStore($spriteContentRect)
  const stageHitTest = useVideoPixelHitTest(windowId)

  const lastDomPointer = useRef<{ x: number; y: number } | null>(null)
  const domProbeFrame = useRef<number | null>(null)

  const probeDomPointer = useCallback((): boolean => {
    const node = mountRef.current

    if (!domHitPassthrough || !node) {
      return false
    }

    const point = lastDomPointer.current
    const rect = node.getBoundingClientRect()

    const inside =
      point !== null && point.x >= rect.left && point.x <= rect.right && point.y >= rect.top && point.y <= rect.bottom

    const capture = !hidden && (dragRef.current !== null || (inside && stageHitTest(point.x, point.y)))
    const next = capture ? 'auto' : 'none'

    if (node.style.pointerEvents !== next) {
      node.style.pointerEvents = next
    }

    return inside && !hidden
  }, [domHitPassthrough, hidden, stageHitTest])

  // 桌面内透明像素透给下层 DOM；窗口精灵仍沿用原生窗口捕获。
  useEffect(() => {
    if (!domHitPassthrough) {
      return
    }

    const tick = (): void => {
      domProbeFrame.current = null

      if (probeDomPointer() && document.hasFocus()) {
        domProbeFrame.current = requestAnimationFrame(tick)
      }
    }

    const update = (event: globalThis.PointerEvent): void => {
      lastDomPointer.current = { x: event.clientX, y: event.clientY }
      probeDomPointer()

      if (domProbeFrame.current === null) {
        tick()
      }
    }

    const pause = (): void => {
      if (domProbeFrame.current !== null) {
        cancelAnimationFrame(domProbeFrame.current)
        domProbeFrame.current = null
      }
    }

    const resume = (): void => {
      if (domProbeFrame.current === null) {
        tick()
      }
    }

    window.addEventListener('blur', pause)
    window.addEventListener('focus', resume)
    document.addEventListener('pointermove', update, true)
    document.addEventListener('pointerover', update, true)

    return () => {
      window.removeEventListener('blur', pause)
      window.removeEventListener('focus', resume)
      document.removeEventListener('pointermove', update, true)
      document.removeEventListener('pointerover', update, true)

      pause()
    }
  }, [domHitPassthrough, probeDomPointer])

  useLayoutEffect(() => {
    probeDomPointer()
  }, [probeDomPointer, pos, scale, peek, content])

  const pendingPosRef = useRef<{ x: number; y: number } | null>(null)
  const dragRafRef = useRef<number | null>(null)
  const displayProbeAtRef = useRef(0)
  const lastDragPositionRef = useRef<{ x: number; y: number } | null>(null)
  const lastDragPointRef = useRef<{ x: number; y: number } | null>(null)

  const stageRect = useCallback(
    (el: HTMLElement): DOMRect | null => {
      if (hidden) {
        return null
      }

      const rect = el.getBoundingClientRect()

      return rect.width === 0 || rect.height === 0 ? null : rect
    },
    [hidden]
  )

  // 命中按渲染路径精化：视频走 alpha 遮罩查表，缺席才回退整矩形——否则空白区会挡住底下应用的点击。
  useInteractiveRegion(SPRITE_REGION_ID, mountRef, stageRect, stageHitTest, windowId)
  useSpriteBodyGesture(bodyRef)

  const finishGesture = useCallback((cancelled: boolean) => {
    const drag = dragRef.current
    dragRef.current = null

    if (longPressTimerRef.current !== null) {
      clearTimeout(longPressTimerRef.current)
      longPressTimerRef.current = null
    }

    if (dragRafRef.current !== null) {
      cancelAnimationFrame(dragRafRef.current)
      dragRafRef.current = null
    }

    if (cancelled && tapTimerRef.current !== null) {
      clearTimeout(tapTimerRef.current)
      tapTimerRef.current = null
    }

    if (cancelled) {
      lastTapRef.current = 0
    }

    if (!drag) {
      pendingPosRef.current = null

      return null
    }

    lastDragPointRef.current = drag.moved ? { x: drag.lastX, y: drag.lastY } : null

    lastDragPositionRef.current = drag.moved
      ? { x: drag.originX + drag.lastX - drag.startX, y: drag.originY + drag.lastY - drag.startY }
      : null

    if (drag.moved) {
      if (pendingPosRef.current) {
        updateDragPosition(pendingPosRef.current)
      }

      endDragAt(cancelled ? $spatialPos.get() : (lastDragPositionRef.current ?? $spatialPos.get()), cancelled)
    }

    pendingPosRef.current = null

    // 先清空手势再释放 DOM 捕获，避免 lostpointercapture 重复提交。
    try {
      if (drag.target.hasPointerCapture(drag.pointerId)) {
        drag.target.releasePointerCapture(drag.pointerId)
      }
    } finally {
      drag.releaseCapture()
    }

    if (drag.moved && !cancelled) {
      handleDragEndInteraction()
    }

    return drag
  }, [])

  useEffect(() => {
    const cancel = () => {
      finishGesture(true)
    }

    window.addEventListener('blur', cancel)

    return () => {
      window.removeEventListener('blur', cancel)
      finishGesture(true)
      gestureGenerationRef.current += 1
    }
  }, [finishGesture])

  useEffect(() => {
    if (hidden) {
      finishGesture(true)
    }
  }, [hidden, finishGesture])

  // 精灵窗只占一块显示器，搬精灵须移动窗口并对齐光标所在显示器；只有 POSITION 按原点 delta 平移，拖拽参考点不能动—— pointer 事件已在新视口空间，再平移 start 会把精灵钉在旧坐标上。
  const probeDisplaySwitch = useCallback((): void => {
    const now = performance.now()

    if (displayProbePendingRef.current || now - displayProbeAtRef.current < DISPLAY_SWITCH_PROBE_MS) {
      return
    }

    displayProbeAtRef.current = now
    displayProbePendingRef.current = true
    const generation = gestureGenerationRef.current

    void window.spiritagent.sprite
      .moveToCursorDisplay()
      .then(switched => {
        if (!switched || generation !== gestureGenerationRef.current) {
          return
        }

        const { cursor, from, to } = switched
        const dx = from.x - to.x
        const dy = from.y - to.y
        const d = dragRef.current

        // 窗口跳转前后坐标分属旧/新空间（相差原点 delta）；最新拖拽点已在新空间时拖拽公式自会算出平移位置，再平移一次会在一帧内双重应用 delta。
        const point = d?.moved ? { x: d.lastX, y: d.lastY } : lastDragPointRef.current

        if (
          point &&
          Math.hypot(point.x - (cursor.x - to.x), point.y - (cursor.y - to.y)) <=
            Math.hypot(point.x - (cursor.x - from.x), point.y - (cursor.y - from.y))
        ) {
          return
        }

        const dragging = d?.moved === true

        // 拖拽释放比显示器切换早到时也要重映射静止位置，否则精灵停在旧视口坐标上；自主移动已算出新空间位置时跳过。
        if (!dragging && ($spatialLocomotion.get() !== 'still' || $surfaceOpen.get() !== null)) {
          return
        }

        if (pendingPosRef.current) {
          pendingPosRef.current.x += dx
          pendingPosRef.current.y += dy
        }

        // 旧屏位置可能已被边界钳制；使用指针计算的原始位置，避免把丢失的位移带入新屏。
        const raw = d?.moved
          ? { x: d.originX + d.lastX - d.startX, y: d.originY + d.lastY - d.startY }
          : lastDragPositionRef.current

        if (!raw) {
          return
        }

        updateDragPosition({ x: raw.x + dx, y: raw.y + dy })

        if (!dragging) {
          const next = $spatialPos.get()
          $homePosition.set(next)
          void window.spiritagent.sprite.setPosition(next)
        }
      })
      .catch(error => console.warn('Sprite display switch failed', error))
      .finally(() => {
        displayProbePendingRef.current = false
      })
  }, [])

  // 文件投喂（DESIGN「拖拽与直接交互」）：纯媒体进轻语，含非媒体文件时整批进生活空间。
  const handleDrop = (fileList: FileList | null | undefined): void => {
    const paths = resolveDroppedFiles(fileList)

    if (paths.length === 0) {
      return
    }

    // 接取反馈：下沉承接形变 + 爱心/音符粒子（本地机械反馈，不调用推理）。
    emitVfx('heart', { nx: 0.5, ny: 0.25, count: 3 })
    emitVfx('music_notes', { nx: 0.35, ny: 0.15, count: 3 })
    playSpriteGesture({ kind: 'catch' })
    setSpriteState('interacting', { durationMs: 2000 })
    clearExternalAttachment()

    if (paths.every(path => MEDIA_DROP_PATH_RE.test(path))) {
      // 同窗轻语：先推本地附件再打开，订阅挂载时按 nonce 消费。
      pushExternalAttachment(paths)
      openWhisper()

      return
    }

    // 跨窗：生活空间是独立 BrowserWindow，atom 互不可见，经主进程信箱转交；失败时提示重新拖入仍打开生活空间。
    void window.spiritagent.chat
      .setPendingFeed(paths)
      .catch((error: unknown) => {
        log.warn('sprite-stage', 'Dropped files handoff failed', error)
        notifyError(error, getStrings().chat.filesHandoffFailed)
      })
      .then(() => requestOpenSurface('living'))
      .catch((error: unknown) => log.warn('sprite-stage', 'Could not open living space', error))
  }

  const onPointerDown = (e: PointerEvent<HTMLDivElement>): void => {
    if (hidden || dragRef.current || !stageHitTest(e.clientX, e.clientY)) {
      return
    }

    if (e.button !== 0) {
      return
    }

    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    const releaseCapture = holdWindowMouseCapture(windowId)
    gestureGenerationRef.current += 1
    lastDragPointRef.current = null
    lastDragPositionRef.current = null
    pendingPosRef.current = null
    const origin = $spatialPos.get()
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      originX: origin.x,
      originY: origin.y,
      moved: false,
      lastX: e.clientX,
      lastY: e.clientY,
      pointerId: e.pointerId,
      target: e.currentTarget,
      releaseCapture,
      longPressed: false
    }
    cancelMovement()

    // 启动独立 long-press 计时器：与 drag 完全解耦，drag 触发后会清掉。
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current)
    }

    longPressTimerRef.current = setTimeout(() => {
      longPressTimerRef.current = null
      const d = dragRef.current

      if (d && !d.moved) {
        d.longPressed = true
        // 长按形变 + 粒子，与拖拽释放的落地形变区分；属本地机械反馈，不调用推理。
        emitVfx('heart', { nx: 0.5, ny: 0.25, count: 2 })
        playSpriteGesture({ kind: 'squeeze' })
        setSpriteState('interacting', { durationMs: 800 })
      }
    }, LONG_PRESS_MS)
  }

  const onPointerMove = (e: PointerEvent<HTMLDivElement>): void => {
    if (hidden) {
      return
    }

    const d = dragRef.current

    if (!d || d.pointerId !== e.pointerId) {
      return
    }

    if ((e.buttons & 1) === 0) {
      finishGesture(true)

      return
    }

    const dx = e.clientX - d.startX
    const dy = e.clientY - d.startY

    if (!d.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
      d.moved = true
      startDrag()
      lastTapRef.current = 0

      if (tapTimerRef.current !== null) {
        clearTimeout(tapTimerRef.current)
        tapTimerRef.current = null
      }

      // drag 一旦开始就放弃 long-press 等待：与拖拽是互斥的两条交互通道。
      if (longPressTimerRef.current) {
        clearTimeout(longPressTimerRef.current)
        longPressTimerRef.current = null
      }
    }

    if (d.moved) {
      if (
        allowDisplaySwitch &&
        (e.clientX < 0 || e.clientX > window.innerWidth || e.clientY < 0 || e.clientY > window.innerHeight)
      ) {
        probeDisplaySwitch()
      }

      d.lastX = e.clientX
      d.lastY = e.clientY

      const nextX = Math.round(d.originX + dx)
      const nextY = Math.round(d.originY + dy)

      pendingPosRef.current = { x: nextX, y: nextY }

      if (dragRafRef.current === null) {
        dragRafRef.current = requestAnimationFrame(() => {
          dragRafRef.current = null

          if (pendingPosRef.current) {
            updateDragPosition(pendingPosRef.current)
            pendingPosRef.current = null
          }
        })
      }
    }
  }

  const onPointerUp = (e: PointerEvent<HTMLDivElement>): void => {
    const active = dragRef.current

    if (!active || active.pointerId !== e.pointerId || e.button !== 0) {
      return
    }

    if (active.moved) {
      active.lastX = e.clientX
      active.lastY = e.clientY
      pendingPosRef.current = {
        x: Math.round(active.originX + e.clientX - active.startX),
        y: Math.round(active.originY + e.clientY - active.startY)
      }
    }

    const drag = finishGesture(hidden)

    if (!drag || hidden || drag.moved || drag.longPressed) {
      return
    }

    const now = Date.now()

    if (onDoubleTap && now - lastTapRef.current < DOUBLE_TAP_MS) {
      if (tapTimerRef.current !== null) {
        clearTimeout(tapTimerRef.current)
        tapTimerRef.current = null
      }

      lastTapRef.current = 0
      onDoubleTap()

      return
    }

    lastTapRef.current = now

    // 存在双击回调时，单击延迟一拍再触发；在窗口内到达的第二次抬起会取消本计时器。
    if (onDoubleTap) {
      if (tapTimerRef.current !== null) {
        clearTimeout(tapTimerRef.current)
      }

      tapTimerRef.current = setTimeout(() => {
        tapTimerRef.current = null
        onTap?.()
      }, DOUBLE_TAP_MS)

      return
    }

    onTap?.()
  }

  // 指针被系统取消或捕获丢失时，按取消收尾当前手势。
  const onPointerInterrupted = (e: PointerEvent<HTMLDivElement>): void => {
    if (dragRef.current?.pointerId === e.pointerId) {
      finishGesture(true)
    }
  }

  const spriteW = getBaseSpriteWidth()
  const spriteH = getBaseSpriteHeight()
  const maskRects = peekMaskRects(peek, pos, scale, spriteW, spriteH)
  // 形变与情绪放大以内容脚底中点为原点，透明留白不参与。
  const bodyOrigin = `${((content?.left ?? 0) + (content?.right ?? 1)) * 50}% ${(content?.bottom ?? 1) * 100}%`

  return (
    <div className="fixed inset-0" data-sprite-stage style={{ pointerEvents: 'none' }}>
      {peek ? (
        <svg aria-hidden="true" className="pointer-events-none absolute size-0" focusable="false">
          <defs>
            <mask height={spriteH} id="spiritagent-peek-mask" maskUnits="userSpaceOnUse" width={spriteW}>
              <rect fill="white" height={spriteH} width={spriteW} x="0" y="0" />
              {maskRects.map((rect, index) => (
                <rect
                  fill="black"
                  height={rect.bottom - rect.top}
                  key={index}
                  width={rect.right - rect.left}
                  x={rect.left}
                  y={rect.top}
                />
              ))}
            </mask>
          </defs>
        </svg>
      ) : null}
      <SpriteTargetCue hidden={hidden} />
      <div
        className="absolute transition-opacity duration-200"
        onContextMenu={e => {
          if (hidden) {
            return
          }

          e.preventDefault()
          onContextMenu?.(e)
        }}
        onDragOver={e => {
          e.preventDefault()
        }}
        onDrop={e => {
          e.preventDefault()

          if (onDropFiles) {
            onDropFiles(e.dataTransfer.files)
          } else {
            handleDrop(e.dataTransfer.files)
          }
        }}
        onLostPointerCapture={onPointerInterrupted}
        onPointerCancel={onPointerInterrupted}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        ref={mountRef}
        style={{
          left: 0,
          top: 0,
          width: `${spriteW}px`,
          height: `${spriteH}px`,
          pointerEvents: hidden ? 'none' : 'auto',
          touchAction: 'none',
          visibility: hidden ? 'hidden' : 'visible',
          opacity: hidden ? 0 : 1,
          mask: peek ? 'url(#spiritagent-peek-mask)' : undefined,
          WebkitMask: peek ? 'url(#spiritagent-peek-mask)' : undefined,
          transform: `translate3d(${pos.x}px, ${pos.y}px, 0px) scale(${scale})`,
          transformOrigin: 'top left',
          willChange: 'transform, opacity'
        }}
      >
        <FootGlow />
        <div
          className="sprite-body"
          data-expression-boost={expressionBoost ? '' : undefined}
          style={{ transformOrigin: bodyOrigin }}
        >
          <div className="absolute inset-0" ref={bodyRef} style={{ transformOrigin: bodyOrigin }}>
            {children}
          </div>
        </div>
        <SpriteVfxOverlay />
      </div>
    </div>
  )
}
