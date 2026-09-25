import { useStore } from '@nanostores/react'
import { type PointerEvent, type ReactNode, useCallback, useEffect, useRef } from 'react'

import { handleDragEndInteraction } from '@/modules/character'
import {
  $homePosition,
  $spatialLocomotion,
  $spatialPos,
  $spatialScale,
  $spriteAction,
  cancelMovement,
  endDragAt,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  setSpriteState,
  startDrag,
  updateDragPosition
} from '@/modules/character'
import { emitVfx, SpriteVfxOverlay } from '@/modules/character'
import { FootGlow } from '@/modules/character'
import { useVideoPixelHitTest } from '@/modules/character/rendering/video'
import { clearExternalAttachment, pushExternalAttachment } from '@/modules/conversation'
import { resolveDroppedFiles } from '@/shared/lib/file-drop'
import { holdWindowMouseCapture, useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { $surfaceOpen, requestOpenSurface } from '@/shared/store/surfaces'

import { openWhisper } from '../whisper'

interface SpriteStageProps {
  children: ReactNode
  onTap?: (nx: number, ny: number) => void
  onDoubleTap?: () => void
  onContextMenu?: (e: React.MouseEvent) => void
  hidden?: boolean
}

// 12px 是为了避免触控板微抖动被误判为拖拽、把双击吞掉。
const DRAG_THRESHOLD = 12
const DOUBLE_TAP_MS = 320
// 长按阈值（DESIGN §6.3）：按住未移动 ≥ 500ms 触发 long_press 精灵动作与粒子；
// 拖拽一旦启动即取消等待，两条交互通道互斥。
const LONG_PRESS_MS = 500
// 投喂分流：纯图片/视频走轻语快速回复；混有其它文件时整批进生活空间。
const MEDIA_DROP_PATH_RE = /\.(png|jpe?g|gif|webp|bmp|svg|avif|mp4|mov|webm|m4v|avi|mkv)$/i

// 一旦光标跨到另一块显示器，pointer capture 会持续投递跨视口坐标；
// 探测主进程的频率最多为此间隔。
const DISPLAY_SWITCH_PROBE_MS = 200

const SPRITE_REGION_ID = 'sprite-stage'

export function SpriteStage({
  children,
  onTap,
  onDoubleTap,
  onContextMenu,
  hidden = false
}: SpriteStageProps): React.JSX.Element {
  const mountRef = useRef<HTMLDivElement>(null)

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
  // 命中按渲染路径精化：视频走 alpha 遮罩查表；缺席（桌面蛋 / 加载空挡）才回退整矩形。
  const stageHitTest = useVideoPixelHitTest()

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

  // 命中按渲染路径精化：视频走 alpha 遮罩查表；缺席（桌面蛋 / 加载空挡）才回退整矩形
  // ——否则矩形空白区会挡住底下应用的点击。
  useInteractiveRegion(SPRITE_REGION_ID, mountRef, stageRect, stageHitTest)

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

      endDragAt($spatialPos.get(), cancelled)
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

  // 精灵窗口只占一块显示器；要把精灵搬到另一块显示器上就要移动窗口。
  // 主进程会把窗口对齐到光标所在显示器并返回两个窗口原点。
  // 只有精灵的 POSITION 需要按原点 delta 平移——拖拽参考点不能动：
  // 切换后到达的 pointer 事件在 NEW 视口空间里（client 本身就跳过了同样的 delta），
  // 所以 origin + (client - start) 会自然产出平移后的值；再平移 start 反而
  // 会把精灵钉在旧视口坐标上、甩到新显示器边缘。
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

        // 窗口跳转前抓到的坐标是旧空间，跳转后是新空间；两者相差原点 delta
        // （几百像素），但主进程读取光标之后光标只动了几个像素。
        // 如果最新的拖拽点已经在新空间，拖拽公式自己就能算出平移后的位置——
        // 再平移一次会让 delta 在一帧内被双重应用。
        const point = d?.moved ? { x: d.lastX, y: d.lastY } : lastDragPointRef.current

        if (
          point &&
          Math.hypot(point.x - (cursor.x - to.x), point.y - (cursor.y - to.y)) <=
            Math.hypot(point.x - (cursor.x - from.x), point.y - (cursor.y - from.y))
        ) {
          return
        }

        const dragging = d?.moved === true

        // 拖拽释放比显示器切换早到——也要重映射静止位置，否则精灵会停在旧视口
        // 坐标上（新显示器上看不见）。自主移动已经算出新空间位置时跳过。
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

  // 文件投喂（DESIGN §6.3）：解析真实文件路径并推到 chat-dock。
  // 纯媒体进轻语（快速看图/视频）；含非媒体文件时整批进生活空间。
  const handleDrop = (fileList: FileList | null | undefined): void => {
    const paths = resolveDroppedFiles(fileList)

    if (paths.length === 0) {
      return
    }

    // 接取动效（DESIGN §6.3「触发接取动效与爱心/音符反馈」）：抬手接住 + 爱心/音符粒子
    emitVfx('heart', { nx: 0.5, ny: 0.25, count: 3 })
    emitVfx('music_notes', { nx: 0.35, ny: 0.15, count: 3 })
    $spriteAction.set('present_right')
    setSpriteState('interacting', { durationMs: 2000 })
    clearExternalAttachment()

    if (paths.every(path => MEDIA_DROP_PATH_RE.test(path))) {
      // 同窗轻语：先推本地附件再打开，订阅挂载时按 nonce 消费。
      pushExternalAttachment(paths)
      openWhisper()

      return
    }

    // 跨窗：生活空间是独立 BrowserWindow，内存 atom 互不可见——经主进程信箱转交。
    void window.spiritagent.chat
      .setPendingFeed(paths)
      .then(() => requestOpenSurface('living'))
      .catch(() => {
        // 信箱写入失败时仍打开表面，避免用户以为投喂被吞掉却无后续。
        void requestOpenSurface('living')
      })
  }

  const onPointerDown = (e: PointerEvent<HTMLDivElement>): void => {
    if (hidden || dragRef.current || !stageHitTest(e.clientX, e.clientY)) {
      return
    }

    // 只在按下左键时捕获
    if (e.button !== 0) {
      return
    }

    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    const releaseCapture = holdWindowMouseCapture()
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
        // 触发时附带 VFX + sprite action：与拖拽的 drag_end 区分。
        // DESIGN §6.3 长按/拖拽与抛掷：「拖拽始终使用本地预制反馈」——长按是
        // 用户主动且未移动，可触发专属 sprite action 让其他模块响应。
        emitVfx('heart', { nx: 0.5, ny: 0.25, count: 2 })
        $spriteAction.set('long_press')
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
      if (e.clientX < 0 || e.clientX > window.innerWidth || e.clientY < 0 || e.clientY > window.innerHeight) {
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
    // 计算归一化坐标 (nx, ny) 透传给 onTap
    const rect = mountRef.current?.getBoundingClientRect()
    const nx = rect && rect.width > 0 ? (e.clientX - rect.left) / rect.width : 0.5
    const ny = rect && rect.height > 0 ? (e.clientY - rect.top) / rect.height : 0.5

    // 存在双击回调时，单击延迟一拍再触发；在窗口内到达的第二次抬起会取消本计时器。
    if (onDoubleTap) {
      if (tapTimerRef.current !== null) {
        clearTimeout(tapTimerRef.current)
      }

      tapTimerRef.current = setTimeout(() => {
        tapTimerRef.current = null
        onTap?.(nx, ny)
      }, DOUBLE_TAP_MS)

      return
    }

    onTap?.(nx, ny)
  }

  const spriteW = getBaseSpriteWidth()
  const spriteH = getBaseSpriteHeight()

  return (
    <div className="fixed inset-0" data-sprite-stage style={{ pointerEvents: 'none' }}>
      <div
        className={`absolute transition-opacity duration-200 ${hidden ? 'pointer-events-none opacity-0 invisible' : 'opacity-100'}`}
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
          void handleDrop(e.dataTransfer?.files)
        }}
        onLostPointerCapture={e => {
          if (dragRef.current?.pointerId === e.pointerId) {
            finishGesture(true)
          }
        }}
        onPointerCancel={e => {
          if (dragRef.current?.pointerId === e.pointerId) {
            finishGesture(true)
          }
        }}
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
          transform: `translate3d(${pos.x}px, ${pos.y}px, 0px) scale(${scale})`,
          transformOrigin: 'top left',
          willChange: 'transform, opacity'
        }}
      >
        <FootGlow />
        {children}
        <SpriteVfxOverlay />
      </div>
    </div>
  )
}
