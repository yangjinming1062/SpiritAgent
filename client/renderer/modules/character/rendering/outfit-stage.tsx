import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'

import {
  $spriteCanvasRect,
  $spriteContentRect,
  $spriteHeadRect,
  $stageOutfit,
  $stageOutfitPresented,
  $stageOutfitStatus,
  baseSpriteSize,
  retryStageOutfit
} from '@/modules/character'
import { RENDERER_TIMINGS } from '@/shared/constants'
import { probeInteractiveRegions, useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { log } from '@/shared/lib/log'
import { $auth } from '@/shared/store/auth'
import { requestOpenSurface } from '@/shared/store/surfaces'
import { useStrings } from '@/shared/strings'

import { EggStage, type EggStageStatus } from './fallback/egg-stage'
import { $mediaHitTest } from './media-hit-test'

/** 透明阈值：alpha 低于该值视为留白，既不计入轮廓也不命中。 */
const ALPHA_FLOOR = 16

interface Portrait {
  url: string
  owner: string
  width: number
  height: number
  pixels: Uint8ClampedArray
  gridWidth: number
  gridHeight: number
  bounds: [number, number, number, number]
}

function readPortrait(image: HTMLImageElement, url: string, owner: string): Portrait {
  const canvas = document.createElement('canvas')
  const ratio = Math.min(1, 512 / Math.max(image.naturalWidth, image.naturalHeight))
  canvas.width = Math.max(1, Math.round(image.naturalWidth * ratio))
  canvas.height = Math.max(1, Math.round(image.naturalHeight * ratio))
  const context = canvas.getContext('2d', { willReadFrequently: true })

  if (!context) {
    throw new Error('Portrait canvas unavailable')
  }

  context.drawImage(image, 0, 0, canvas.width, canvas.height)
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
  let left = canvas.width
  let top = canvas.height
  let right = 0
  let bottom = 0

  for (let y = 0; y < canvas.height; y += 1) {
    for (let x = 0; x < canvas.width; x += 1) {
      if ((pixels[(y * canvas.width + x) * 4 + 3] ?? 0) < ALPHA_FLOOR) {
        continue
      }

      left = Math.min(left, x)
      top = Math.min(top, y)
      right = Math.max(right, x + 1)
      bottom = Math.max(bottom, y + 1)
    }
  }

  if (right <= left || bottom <= top) {
    throw new Error('Portrait is empty')
  }

  return {
    url,
    owner,
    width: image.naturalWidth,
    height: image.naturalHeight,
    pixels,
    gridWidth: canvas.width,
    gridHeight: canvas.height,
    bounds: [left / canvas.width, top / canvas.height, right / canvas.width, bottom / canvas.height]
  }
}

/** 精灵窗与内嵌生活区共用静态立绘；新图解码和 alpha 读取完成后才替换可见图，无图时给出真实兜底状态。 */
export function OutfitStage({
  active = true,
  contentAlign,
  eggSize,
  windowId = 0
}: {
  active?: boolean
  contentAlign?: 'left' | 'right' | 'center'
  eggSize?: string
  windowId?: number
} = {}): React.JSX.Element {
  const outfit = useStore($stageOutfit)
  const sourceStatus = useStore($stageOutfitStatus)
  const auth = useStore($auth)
  const owner = auth.kind === 'authenticated' ? auth.snapshot.accountId : null
  const url = outfit?.url ?? null
  const [portrait, setPortrait] = useState<Portrait | null>(null)
  const [decodeFailed, setDecodeFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const root = useRef<HTMLDivElement>(null)
  const imageRef = useRef<HTMLImageElement>(null)
  const retryRef = useRef<HTMLButtonElement>(null)
  useInteractiveRegion('outfit-load-retry', retryRef, undefined, undefined, windowId)
  const strings = useStrings()
  // 新图地址解析或解码期间保留旧画面，只在确实没有当前穿着时才回到蛋形。
  const shown = outfit && portrait?.owner === owner ? portrait : null
  // 当前影像取不到冻结造型地址（解析或解码失败）同样需要用户可见的重新加载入口。
  const loadFailed = decodeFailed || sourceStatus === 'error'

  useLayoutEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (shown?.url === outfit?.url && imageRef.current?.complete && imageRef.current.naturalWidth > 0) {
        $stageOutfitPresented.set(outfit?.path ?? null)
      }
    })

    return () => {
      cancelAnimationFrame(frame)

      if ($stageOutfitPresented.get() === outfit?.path) {
        $stageOutfitPresented.set(null)
      }
    }
  }, [shown, outfit])

  const egg: EggStageStatus | null = shown
    ? null
    : loadFailed
      ? 'failed'
      : url
        ? 'preparing'
        : sourceStatus === 'missing'
          ? 'unavailable'
          : 'preparing'

  useEffect(() => {
    let current = true

    setDecodeFailed(false)

    if (!owner) {
      setPortrait(null)

      return () => {
        current = false
      }
    }

    if (!url) {
      return () => {
        current = false
      }
    }

    const image = new Image()

    const timeout = window.setTimeout(() => {
      if (current) {
        current = false
        setDecodeFailed(true)
      }
    }, RENDERER_TIMINGS.stageMediaTimeoutMs)

    image.src = url
    void image
      .decode()
      .then(() => {
        if (!current) {
          return
        }

        setPortrait(readPortrait(image, url, owner))
      })
      .catch(error => {
        if (current) {
          setDecodeFailed(true)
          log.warn('outfit-stage', 'Portrait load failed', error)
        }
      })
      .finally(() => window.clearTimeout(timeout))

    return () => {
      current = false
      window.clearTimeout(timeout)
    }
  }, [owner, url, retry])

  useLayoutEffect(() => {
    const element = root.current

    if (!element) {
      return
    }

    const measure = (): void => setSize({ width: element.clientWidth, height: element.clientHeight })
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    measure()

    return () => observer.disconnect()
  }, [])

  let style: React.CSSProperties = { inset: 0, height: '100%', width: '100%', objectFit: 'contain' }

  if (shown && contentAlign && size.width > 0 && size.height > 0) {
    const [left, top, right, bottom] = shown.bounds
    const scale = Math.min(size.width / ((right - left) * shown.width), size.height / ((bottom - top) * shown.height))
    style = {
      width: shown.width * scale,
      height: shown.height * scale,
      maxWidth: 'none',
      left:
        contentAlign === 'left'
          ? -left * shown.width * scale
          : contentAlign === 'right'
            ? size.width - right * shown.width * scale
            : (size.width - (right - left) * shown.width * scale) / 2 - left * shown.width * scale,
      top: size.height - bottom * shown.height * scale
    }
  }

  useLayoutEffect(() => {
    if (!active) {
      return
    }

    if (!shown) {
      $mediaHitTest.set(null)

      if (!contentAlign) {
        $spriteCanvasRect.set(null)
        $spriteContentRect.set(null)
        $spriteHeadRect.set(null)
      }
    } else if (!contentAlign) {
      // 精灵舞台按原比例完整显示：画布留白与主体轮廓分开记录，气泡和形变都以主体为准。
      const stage = baseSpriteSize(window.innerHeight)
      const fit = Math.min(stage.width / shown.width, stage.height / shown.height)
      const width = (shown.width * fit) / stage.width
      const height = (shown.height * fit) / stage.height
      const canvas = { left: (1 - width) / 2, top: (1 - height) / 2, right: (1 + width) / 2, bottom: (1 + height) / 2 }
      const [left, top, right, bottom] = shown.bounds

      const content = {
        left: canvas.left + left * width,
        top: canvas.top + top * height,
        right: canvas.left + right * width,
        bottom: canvas.top + bottom * height
      }

      $spriteCanvasRect.set(canvas)
      $spriteContentRect.set(content)
      $spriteHeadRect.set({ ...content, bottom: content.top + (content.bottom - content.top) * 0.15 })
    }

    if (shown) {
      $mediaHitTest.set((x, y) => {
        const rect = imageRef.current?.getBoundingClientRect()

        if (!rect || rect.width === 0 || rect.height === 0) {
          return false
        }

        const fit = Math.min(rect.width / shown.width, rect.height / shown.height)
        const width = shown.width * fit
        const height = shown.height * fit
        const nx = (x - rect.left - (rect.width - width) / 2) / width
        const ny = (y - rect.top - (rect.height - height) / 2) / height

        if (nx < 0 || nx >= 1 || ny < 0 || ny >= 1) {
          return false
        }

        return (
          (shown.pixels[
            (Math.floor(ny * shown.gridHeight) * shown.gridWidth + Math.floor(nx * shown.gridWidth)) * 4 + 3
          ] ?? 0) >= ALPHA_FLOOR
        )
      })
    }

    probeInteractiveRegions(windowId)

    return () => {
      $mediaHitTest.set(null)

      if (!contentAlign) {
        $spriteCanvasRect.set(null)
        $spriteContentRect.set(null)
        $spriteHeadRect.set(null)
      }
    }
  }, [shown, contentAlign, size, windowId, active])

  const reload = (): void => {
    setRetry(value => value + 1)
    retryStageOutfit()
  }

  return (
    <div className="relative h-full w-full" ref={root}>
      {shown && (
        <img
          alt={outfit?.name ?? ''}
          className="absolute"
          draggable={false}
          ref={imageRef}
          src={shown.url}
          style={style}
        />
      )}
      {egg && (
        <EggStage
          hasRecoveryAction={egg !== 'preparing'}
          message={strings.companion.egg[egg]}
          onStatusAction={() => {
            if (egg === 'unavailable') {
              void requestOpenSurface({ page: 'companion', companionSection: 'visuals' })

              return
            }

            reload()
          }}
          size={eggSize}
          status={egg}
          windowId={windowId}
        />
      )}
      {shown && loadFailed && (
        <button
          className="absolute bottom-1 left-1 rounded-lg bg-surface-card/90 px-2 py-1 text-xs text-muted"
          onClick={reload}
          onPointerDown={event => event.stopPropagation()}
          ref={retryRef}
          type="button"
        >
          {strings.companion.egg.failed} · {strings.common.retry}
        </button>
      )}
    </div>
  )
}
