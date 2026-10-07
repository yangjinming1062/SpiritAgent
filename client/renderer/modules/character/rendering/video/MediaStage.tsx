/** 双缓冲保留旧画面直到图片解码或视频首帧就绪；位置与播放实例分别由 spatial、actions 管理。 */

import { useStore } from '@nanostores/react'
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'

import {
  $actionCatalog,
  $actionCatalogStatus,
  $activePlayInstance,
  $peekPreparation,
  $spatialLocomotion,
  $spatialPeek,
  $spatialPos,
  $spatialScale,
  $spriteCanvasRect,
  $spriteContentRect,
  $spriteHeadRect,
  $viewport,
  type ActionHitmask,
  type ActionPlayInstance,
  baseSpriteSize,
  cancelPeekPreparation,
  commitPeekPreparation,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  isActionStageVisible,
  leavePeekForExpression,
  markPlayInstanceStarted,
  type NormalizedRect,
  observeActionStageVisibility,
  peekMaskRects,
  resolveActionClipUrl,
  resolveHitmask,
  resolveSystemAction,
  restorePeekAfterExpression,
  settlePlayInstance,
  shouldStartInstance,
  type SystemActionKey
} from '@/modules/character'
import { probeInteractiveRegions } from '@/shared/lib/interactive-regions'
import { log } from '@/shared/lib/log'

import { $mediaHitTest } from './media-hit-test'

function useCurrentAction(): SystemActionKey {
  const [deltaXSign, setDeltaXSign] = useState(0)
  const locomotion = useStore($spatialLocomotion)
  const activePeek = useStore($spatialPeek)
  const preparation = useStore($peekPreparation)

  useEffect(() => {
    setDeltaXSign(0)

    if (locomotion !== 'walk') {
      return
    }

    let lastX = $spatialPos.get().x

    return $spatialPos.listen(({ x }) => {
      setDeltaXSign(Math.sign(x - lastX))
      lastX = x
    })
  }, [locomotion])

  return resolveSystemAction({ locomotion, deltaXSign, peekAction: activePeek?.action ?? preparation?.action ?? null })
}

type Presentation =
  | { kind: 'expression'; instance: ActionPlayInstance; mountKey: string }
  | { kind: 'base'; action: SystemActionKey; mountKey: string }

function settleReplacedInstance(previous: ActionPlayInstance | null): void {
  if (previous === null) {
    return
  }

  settlePlayInstance(previous, 'interrupted', 'preempted')
}

function shouldStartVisibleInstance(instance: ActionPlayInstance): boolean {
  if (!shouldStartInstance(instance)) {
    return false
  }

  if (!isActionStageVisible()) {
    settleReplacedInstance(instance)

    return false
  }

  return true
}

function resolvePresentation(
  baseAction: SystemActionKey,
  instance: ActionPlayInstance | null,
  deferExpression = false
): Presentation {
  if (instance !== null && baseAction === 'idle' && !deferExpression) {
    return { kind: 'expression', instance, mountKey: `play:${instance.playId}` }
  }

  return { kind: 'base', action: baseAction, mountKey: `base:${baseAction}` }
}

/** 等到真实解码首帧就绪，旧画面在加载和失败期间继续播放。 */
async function loadVideo(el: HTMLVideoElement, url: string, signal: AbortSignal): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    let settled = false
    let frameCallback: number | null = null

    const finish = (error?: Error): void => {
      if (settled) {
        return
      }

      settled = true
      window.clearTimeout(timer)
      el.removeEventListener('loadeddata', ready)
      el.removeEventListener('error', failed)
      signal.removeEventListener('abort', aborted)

      if (frameCallback !== null) {
        el.cancelVideoFrameCallback(frameCallback)
      }

      if (error) {
        el.pause()
        reject(error)
      } else {
        resolve()
      }
    }

    const ready = (): void => {
      void el.play().then(
        () => {
          if (!settled) {
            frameCallback = el.requestVideoFrameCallback(() => finish())
          }
        },
        () => finish(new Error('Video playback failed'))
      )
    }

    const failed = (): void => finish(new Error('Video decode failed'))
    const aborted = (): void => finish(new Error('Video load cancelled'))
    const timer = window.setTimeout(() => finish(new Error('Video load timed out')), 15000)
    el.addEventListener('loadeddata', ready, { once: true })
    el.addEventListener('error', failed, { once: true })
    signal.addEventListener('abort', aborted, { once: true })

    if (signal.aborted) {
      aborted()

      return
    }

    el.src = url
    el.load()
  })
}

async function loadImage(el: HTMLImageElement, url: string, signal: AbortSignal): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    let settled = false

    const finish = (error?: Error): void => {
      if (settled) {
        return
      }

      settled = true
      window.clearTimeout(timer)
      el.removeEventListener('load', ready)
      el.removeEventListener('error', failed)
      signal.removeEventListener('abort', aborted)

      if (error) {
        reject(error)
      } else {
        resolve()
      }
    }

    const ready = (): void => {
      void el.decode().then(
        () => finish(),
        () => finish(new Error('Image decode failed'))
      )
    }

    const failed = (): void => finish(new Error('Image load failed'))
    const aborted = (): void => finish(new Error('Image load cancelled'))
    const timer = window.setTimeout(() => finish(new Error('Image load timed out')), 15000)
    el.addEventListener('load', ready, { once: true })
    el.addEventListener('error', failed, { once: true })
    signal.addEventListener('abort', aborted, { once: true })

    if (signal.aborted) {
      aborted()

      return
    }

    el.src = url
  })
}

function prepareFirstFrame(el: HTMLVideoElement, signal: AbortSignal): Promise<boolean> {
  return new Promise(resolve => {
    let settled = false
    let callbackId: number | null = null
    const timer = window.setTimeout(() => finish(false), 3000)

    const finish = (ready: boolean): void => {
      if (settled) {
        return
      }

      settled = true
      window.clearTimeout(timer)
      signal.removeEventListener('abort', aborted)

      if (callbackId !== null) {
        el.cancelVideoFrameCallback(callbackId)
      }

      resolve(ready)
    }

    const aborted = (): void => finish(false)
    signal.addEventListener('abort', aborted, { once: true })

    if (signal.aborted) {
      aborted()

      return
    }

    try {
      el.pause()
      el.currentTime = 0
      void el.play().then(
        () => {
          if (!settled) {
            callbackId = el.requestVideoFrameCallback(() => finish(true))
          }
        },
        () => finish(false)
      )
    } catch {
      finish(false)
    }
  })
}

interface MountedClip {
  key: string
  playId: string | null
  started: boolean
}

interface DisplayedClip {
  mediaType: 'image' | 'video'
  bounds: NormalizedRect
  headBounds: NormalizedRect | null
  height: number
  width: number
}

const FULL_CONTENT_RECT: NormalizedRect = [0, 0, 1, 1]
const HEAD_HEIGHT_RATIO = 0.15

interface HitmaskOutline {
  readonly grid: readonly [number, number]
  readonly rows: readonly number[]
}

function mergeHitmaskRows(hitmask: ActionHitmask | null): HitmaskOutline | null {
  if (!hitmask) {
    return null
  }

  if (hitmask.media_type === 'image') {
    return hitmask
  }

  const rows = new Array<number>(hitmask.grid[1]).fill(0)

  for (const frame of hitmask.frames) {
    for (let y = 0; y < rows.length; y += 1) {
      rows[y] |= frame[y] ?? 0
    }
  }

  return { grid: hitmask.grid, rows }
}

function hitmaskContentRect(outline: HitmaskOutline | null, region = FULL_CONTENT_RECT): NormalizedRect | null {
  if (!outline) {
    return null
  }

  const {
    grid: [gridWidth, gridHeight],
    rows
  } = outline

  let left = gridWidth
  let top = gridHeight
  let right = 0
  let bottom = 0

  for (let y = Math.floor(region[1] * gridHeight); y < Math.ceil(region[3] * gridHeight); y += 1) {
    for (let x = Math.floor(region[0] * gridWidth); x < Math.ceil(region[2] * gridWidth); x += 1) {
      if (((rows[y] ?? 0) & (1 << x)) !== 0) {
        left = Math.min(left, x)
        top = Math.min(top, y)
        right = Math.max(right, x + 1)
        bottom = Math.max(bottom, y + 1)
      }
    }
  }

  return right > left && bottom > top
    ? [left / gridWidth, top / gridHeight, right / gridWidth, bottom / gridHeight]
    : null
}

function surfaceMediaStyle(
  displayed: DisplayedClip | null,
  stage: { height: number; width: number },
  align: 'left' | 'right'
): React.CSSProperties | null {
  if (!displayed || stage.width <= 0 || stage.height <= 0) {
    return null
  }

  const [left, top, right, bottom] = displayed.bounds

  const scale = Math.min(
    stage.width / ((right - left) * displayed.width),
    stage.height / ((bottom - top) * displayed.height)
  )

  return {
    height: displayed.height * scale,
    left: align === 'left' ? -left * displayed.width * scale : stage.width - right * displayed.width * scale,
    maxWidth: 'none',
    top: stage.height - bottom * displayed.height * scale,
    width: displayed.width * scale
  }
}

function canCompleteInstance(instance: ActionPlayInstance, mounted: MountedClip | null): boolean {
  return (
    !!mounted?.started &&
    mounted.playId === instance.playId &&
    $activePlayInstance.get()?.generation === instance.generation &&
    isActionStageVisible()
  )
}

export function MediaStage({ contentAlign }: { contentAlign?: 'left' | 'right' } = {}): React.JSX.Element {
  const catalog = useStore($actionCatalog)
  const playInstance = useStore($activePlayInstance)
  const viewport = useStore($viewport)
  const baseAction = useCurrentAction()
  const spatialPeek = useStore($spatialPeek)
  const peekPreparation = useStore($peekPreparation)
  const videos = useRef<[HTMLVideoElement | null, HTMLVideoElement | null]>([null, null])
  const images = useRef<[HTMLImageElement | null, HTMLImageElement | null]>([null, null])
  const front = useRef<number | null>(null)
  const [visible, setVisible] = useState<{ slot: number; geometry: DisplayedClip } | null>(null)
  const visibleGeometry = visible?.geometry ?? null
  const mounted = useRef<MountedClip | null>(null)
  const peekExitGeneration = useRef<number | null>(null)
  const hitmaskRef = useRef<ActionHitmask | null>(null)
  const displayedClips = useRef<[DisplayedClip | null, DisplayedClip | null]>([null, null])
  const contentAlignRef = useRef(contentAlign)
  contentAlignRef.current = contentAlign

  const rootRef = useRef<HTMLDivElement>(null)
  const [stageSize, setStageSize] = useState({ height: 0, width: 0 })
  const canvas = catalog?.manifest.canvas

  useLayoutEffect(() => {
    if (!contentAlign || !rootRef.current) {
      return
    }

    const element = rootRef.current

    const measure = (): void => {
      const { height, width } = element.getBoundingClientRect()
      setStageSize(previous => (previous.width === width && previous.height === height ? previous : { height, width }))
    }

    const observer = new ResizeObserver(measure)
    observer.observe(element)
    measure()

    return () => observer.disconnect()
  }, [contentAlign])

  useEffect(() => {
    const stop = observeActionStageVisibility(visible => {
      if (!visible) {
        settleReplacedInstance($activePlayInstance.get())
      }
    })

    return () => {
      stop()
      settleReplacedInstance($activePlayInstance.get())
    }
  }, [])

  const canvasRect = useMemo(() => {
    if (!canvas) {
      return null
    }

    const stage = baseSpriteSize(viewport.height)
    const width = visibleGeometry?.width ?? canvas.width
    const height = visibleGeometry?.height ?? canvas.height
    const contain = Math.min(stage.width / width, stage.height / height)
    const drawW = (width * contain) / stage.width
    const drawH = (height * contain) / stage.height

    return {
      left: (1 - drawW) / 2,
      top: (1 - drawH) / 2,
      right: (1 + drawW) / 2,
      bottom: (1 + drawH) / 2
    }
  }, [canvas, viewport.height, visibleGeometry])

  const deferExpressionForPeek = playInstance !== null && (spatialPeek !== null || peekPreparation !== null)

  const presentation = useMemo(
    () => resolvePresentation(baseAction, playInstance, deferExpressionForPeek),
    [baseAction, playInstance, deferExpressionForPeek]
  )

  useEffect(() => {
    if (playInstance && (spatialPeek || peekPreparation)) {
      if (peekExitGeneration.current !== playInstance.generation) {
        peekExitGeneration.current = playInstance.generation
        leavePeekForExpression()
      }

      return
    }

    if (!playInstance && !spatialPeek && peekExitGeneration.current !== null) {
      peekExitGeneration.current = null
      void restorePeekAfterExpression()
    }
  }, [playInstance, spatialPeek, peekPreparation])

  const targetClip =
    presentation.kind === 'expression'
      ? presentation.instance.clip
      : (catalog?.clipsBySlot.get(presentation.action) ?? null)

  const idleClip = catalog?.clipsBySlot.get('idle') ?? null
  const clip = targetClip ?? idleClip

  const preparationForClip =
    presentation.kind === 'base' &&
    peekPreparation?.action === presentation.action &&
    peekPreparation.packId === catalog?.packId &&
    clip?.system_slot === peekPreparation.action
      ? peekPreparation
      : null

  // 包、素材版本和播放请求均参与切换键，同一路径的新请求也须重播。
  const clipSwitchKey = clip ? `${clip.media_ref}@${clip.asset_revision}` : 'none'
  const stableMountKey = `${presentation.mountKey}|${catalog?.packId ?? 0}|${clipSwitchKey}`
  const preparationKey = preparationForClip ? `|prepare:${preparationForClip.generation}` : ''
  const mountKey = `${stableMountKey}${preparationKey}`

  const prevInstanceRef = useRef<ActionPlayInstance | null>(null)
  useEffect(() => {
    const currentInstance =
      presentation.kind === 'expression' ? presentation.instance : deferExpressionForPeek ? playInstance : null

    if (currentInstance !== prevInstanceRef.current) {
      settleReplacedInstance(prevInstanceRef.current)
    }

    prevInstanceRef.current = currentInstance
  }, [deferExpressionForPeek, playInstance, presentation])

  useEffect(() => {
    if (!catalog || !clip) {
      return
    }

    if (mounted.current?.key === mountKey) {
      return
    }

    // 视频基础动作持续循环，表达仅在素材可循环且请求了多次时循环。
    const loop =
      clip.media_type === 'video' &&
      (presentation.kind === 'base' || (clip.loopable && presentation.instance.repeatCount > 1))

    const instance = presentation.kind === 'expression' ? presentation.instance : null
    const controller = new AbortController()
    let pauseTimer = 0

    const slot = front.current === 0 ? 1 : 0
    const elements = videos.current
    const imageElements = images.current
    const video = elements[slot]
    const image = imageElements[slot]
    const el = clip.media_type === 'image' ? image : video

    void (async () => {
      if (!el) {
        return
      }

      try {
        const url = await resolveActionClipUrl(catalog, clip)

        if (controller.signal.aborted) {
          return
        }

        if (!url) {
          throw new Error('Action asset unavailable')
        }

        if (video) {
          video.pause()
        }

        if (clip.media_type === 'video' && video) {
          video.loop = loop
        }

        const mediaLoad =
          clip.media_type === 'image' && image
            ? loadImage(image, url, controller.signal)
            : video
              ? loadVideo(video, url, controller.signal)
              : Promise.reject(new Error('Media element unavailable'))

        const [, hitmask] = await Promise.all([mediaLoad, resolveHitmask(clip)])

        if (controller.signal.aborted) {
          return
        }

        // 表达实例从头播放；真实可见后才上报 started（备用播放器预热不计）。
        if (instance !== null) {
          if (!video || !(await prepareFirstFrame(video, controller.signal))) {
            throw new Error('First video frame unavailable')
          }

          if (!shouldStartVisibleInstance(instance)) {
            return
          }
        }

        const showFirstFrame = (): void => {
          const previous = front.current
          front.current = slot
          const outline = mergeHitmaskRows(hitmask)
          const silhouette = hitmaskContentRect(outline)
          const bounds = clip.content_rect ?? silhouette ?? FULL_CONTENT_RECT
          const headRegion = silhouette ?? bounds

          // 取轮廓上部的 alpha 范围，避免裙摆和张开的手臂把头边气泡推远；整段共用以免逐帧抖动。
          const headBounds = hitmaskContentRect(outline, [
            headRegion[0],
            headRegion[1],
            headRegion[2],
            headRegion[1] + (headRegion[3] - headRegion[1]) * HEAD_HEIGHT_RATIO
          ])

          const geometry = {
            mediaType: clip.media_type,
            bounds,
            headBounds,
            height:
              clip.media_type === 'image' ? (image?.naturalHeight ?? clip.height) : (video?.videoHeight ?? clip.height),
            width: clip.media_type === 'image' ? (image?.naturalWidth ?? clip.width) : (video?.videoWidth ?? clip.width)
          }

          displayedClips.current[slot] = geometry
          mounted.current = {
            key: preparationForClip ? stableMountKey : mountKey,
            playId: instance?.playId ?? null,
            started: false
          }
          hitmaskRef.current = hitmask
          // 遮挡与播放器同一帧提交，不把淡出的完整身体套进探身蒙版。
          flushSync(() => {
            setVisible({ slot, geometry })
          })

          if (instance !== null) {
            window.requestAnimationFrame(() => {
              if (
                front.current === slot &&
                mounted.current?.playId === instance.playId &&
                shouldStartVisibleInstance(instance)
              ) {
                mounted.current.started = true
                markPlayInstanceStarted(instance)
              }
            })
          }

          pauseTimer = window.setTimeout(() => {
            if (previous !== null && front.current !== previous) {
              elements[previous]?.pause()
            }
          }, 140)
        }

        if (preparationForClip) {
          if (
            !(await commitPeekPreparation(
              preparationForClip.action,
              preparationForClip.generation,
              () =>
                video && clip.media_type === 'video'
                  ? prepareFirstFrame(video, controller.signal)
                  : Promise.resolve(true),
              showFirstFrame
            ))
          ) {
            if (!controller.signal.aborted) {
              video?.pause()
            }
          }
        } else {
          showFirstFrame()
        }
      } catch (error) {
        if (controller.signal.aborted) {
          return
        }

        log.warn('media-stage', 'Could not present action', error)

        if (instance !== null) {
          settlePlayInstance(instance, 'rejected', 'load failed')
        } else if (preparationForClip) {
          cancelPeekPreparation(preparationForClip.action, preparationForClip.generation)
        } else if (front.current === null) {
          // 首帧失败由外层回落；已有画面则继续保留。
          $actionCatalogStatus.set('unavailable')
        }
      }
    })()

    return () => {
      controller.abort()
      window.clearTimeout(pauseTimer)

      for (let index = 0; index < elements.length; index += 1) {
        if (index !== front.current) {
          elements[index]?.pause()
          imageElements[index]?.removeAttribute('src')
        }
      }
    }
  }, [catalog, clip, mountKey, preparationForClip, presentation, stableMountKey])

  useLayoutEffect(() => {
    probeInteractiveRegions()
  }, [contentAlign, spatialPeek, stageSize, visible])

  // el.loop 不触发 ended，按 currentTime 回绕统计轮数。
  useEffect(() => {
    const instance = presentation.kind === 'expression' ? presentation.instance : null

    if (instance === null || instance.repeatCount <= 1 || !instance.clip.loopable) {
      return
    }

    let played = 0
    const elements = videos.current

    const handleLoopEnd = (): void => {
      if (!canCompleteInstance(instance, mounted.current)) {
        return
      }

      played += 1

      if (played >= instance.repeatCount) {
        settlePlayInstance(instance, 'completed', '', played * instance.clip.duration_ms)
      }
    }

    let lastTime = 0

    const handleTimeUpdate = (event: Event): void => {
      const el = front.current === null ? null : elements[front.current]

      if (!el || event.currentTarget !== el || mounted.current?.playId !== instance.playId) {
        return
      }

      if (lastTime > el.currentTime) {
        handleLoopEnd()
      }

      lastTime = el.currentTime
    }

    for (const el of elements) {
      el?.addEventListener('timeupdate', handleTimeUpdate)
    }

    return () => {
      for (const el of elements) {
        el?.removeEventListener('timeupdate', handleTimeUpdate)
      }
    }
  }, [presentation])

  // 只接收当前可见实例的结束事件。
  useEffect(() => {
    const elements = videos.current
    const instance = presentation.kind === 'expression' ? presentation.instance : null

    const handleEnded = (event: Event): void => {
      if (
        !instance ||
        front.current === null ||
        event.currentTarget !== elements[front.current] ||
        !canCompleteInstance(instance, mounted.current)
      ) {
        return
      }

      settlePlayInstance(instance, 'completed', '', Math.round(instance.clip.duration_ms))
    }

    for (const el of elements) {
      el?.addEventListener('ended', handleEnded)
    }

    return () => {
      for (const el of elements) {
        el?.removeEventListener('ended', handleEnded)
      }
    }
  }, [presentation])

  useEffect(() => {
    $spriteCanvasRect.set(canvasRect)
  }, [canvasRect])

  useEffect(() => {
    if (!canvas || !canvasRect) {
      $spriteContentRect.set(null)
      $spriteHeadRect.set(null)

      return
    }

    // 轮廓与头部锚点共用媒体等比适配后的舞台坐标。
    const drawW = canvasRect.right - canvasRect.left
    const drawH = canvasRect.bottom - canvasRect.top

    const toStageRect = (bounds: NormalizedRect) => ({
      left: canvasRect.left + bounds[0] * drawW,
      top: canvasRect.top + bounds[1] * drawH,
      right: canvasRect.left + bounds[2] * drawW,
      bottom: canvasRect.top + bounds[3] * drawH
    })

    $spriteContentRect.set(toStageRect(visibleGeometry?.bounds ?? FULL_CONTENT_RECT))
    $spriteHeadRect.set(visibleGeometry?.headBounds ? toStageRect(visibleGeometry.headBounds) : null)
  }, [canvas, canvasRect, visibleGeometry])

  useEffect(
    () => () => {
      $spriteCanvasRect.set(null)
      $spriteContentRect.set(null)
      $spriteHeadRect.set(null)
    },
    []
  )

  useEffect(() => {
    $mediaHitTest.set((px, py) => {
      const slot = front.current
      const geometry = slot === null ? null : displayedClips.current[slot]
      const video = slot === null ? null : videos.current[slot]
      const el = slot === null ? null : geometry?.mediaType === 'image' ? images.current[slot] : video
      const hitmask = hitmaskRef.current
      const rect = el?.getBoundingClientRect()

      // 遮挡先于 alpha 缺失回退，隐藏区域始终穿透。
      const pos = $spatialPos.get()
      const stageScale = $spatialScale.get()
      const masked = peekMaskRects($spatialPeek.get(), pos, stageScale, getBaseSpriteWidth(), getBaseSpriteHeight())
      const localX = (px - pos.x) / stageScale
      const localY = (py - pos.y) / stageScale

      if (
        masked.some(
          block => localX >= block.left && localX < block.right && localY >= block.top && localY < block.bottom
        )
      ) {
        return false
      }

      if (!el || !rect || !geometry?.width || !geometry.height) {
        return contentAlignRef.current ? false : null
      }

      const scale = Math.min(rect.width / geometry.width, rect.height / geometry.height)
      const width = geometry.width * scale
      const height = geometry.height * scale
      const nx = (px - rect.left - (rect.width - width) / 2) / width
      const ny = (py - rect.top - (rect.height - height) / 2) / height

      if (nx < 0 || nx >= 1 || ny < 0 || ny >= 1) {
        return false
      }

      if (!hitmask) {
        const bounds = geometry.bounds

        return contentAlignRef.current && bounds
          ? nx >= bounds[0] && nx < bounds[2] && ny >= bounds[1] && ny < bounds[3]
          : null
      }

      const [gw, gh] = hitmask.grid
      const col = Math.floor(nx * gw)
      const row = Math.floor(ny * gh)

      const sample =
        hitmask.media_type === 'image'
          ? hitmask.rows
          : hitmask.frames[Math.min(hitmask.frames.length - 1, Math.floor((video?.currentTime ?? 0) * hitmask.fps))]

      return ((sample?.[row] ?? 0) & (1 << col)) !== 0
    })
    // React 卸载时先清空 callback refs，保留元素快照才能释放仍在加载的媒体。
    const elements = [...videos.current]
    const imageElements = [...images.current]

    return () => {
      $mediaHitTest.set(null)

      for (const el of elements) {
        if (!el) {
          continue
        }

        el.pause()
        el.removeAttribute('src')
        el.load()
      }

      for (const el of imageElements) {
        el?.removeAttribute('src')
      }
    }
  }, [])

  return (
    <div className="relative h-full w-full" ref={rootRef}>
      {[0, 1].map(slot => {
        const layout = contentAlign ? surfaceMediaStyle(displayedClips.current[slot], stageSize, contentAlign) : null
        const className = contentAlign ? 'absolute object-contain' : 'absolute inset-0 h-full w-full object-contain'

        const style = {
          ...(contentAlign ? (layout ?? { height: '100%', inset: 0, width: '100%' }) : {}),
          transition: spatialPeek || peekPreparation ? 'none' : 'opacity 120ms linear'
        }

        return (
          <React.Fragment key={slot}>
            <video
              className={className}
              muted
              playsInline
              preload="auto"
              ref={el => {
                videos.current[slot] = el
              }}
              style={{ ...style, opacity: visible?.slot === slot && visible.geometry.mediaType === 'video' ? 1 : 0 }}
            />
            <img
              alt=""
              className={className}
              draggable={false}
              ref={el => {
                images.current[slot] = el
              }}
              style={{ ...style, opacity: visible?.slot === slot && visible.geometry.mediaType === 'image' ? 1 : 0 }}
            />
          </React.Fragment>
        )
      })}
    </div>
  )
}
