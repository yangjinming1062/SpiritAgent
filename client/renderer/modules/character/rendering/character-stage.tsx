import { useStore } from '@nanostores/react'
import type React from 'react'
import { useLayoutEffect, useRef, useState } from 'react'

import {
  $spriteCanvasRect,
  $spriteContentRect,
  $spriteFootAnchor,
  $spriteHeadRect,
  $windowActorMedia,
  $windowActorPlayback,
  isCharacterMediaVisible,
  observeCharacterStageVisibility,
  type WindowActorMedia
} from '@/modules/character'
import { probeInteractiveRegions } from '@/shared/lib/interactive-regions'
import { log } from '@/shared/lib/log'
import { acquireStageVideo } from '@/shared/lib/stage-video-cache'
import { createStageVideoLayer } from '@/shared/lib/stage-video-layer'
import { $auth } from '@/shared/store/auth'
import { $presentation } from '@/shared/store/presentation'

import { $mediaHitTest } from './media-hit-test'
import { OutfitStage } from './outfit-stage'

/** 窗口精灵与生活区复用同一主体层播放器；静态造型在首帧准备期间继续显示。 */
export function CharacterStage({
  contentAlign,
  eggSize,
  windowId = 0
}: {
  contentAlign?: 'left' | 'right'
  eggSize?: string
  windowId?: number
} = {}): React.JSX.Element {
  const media = useStore($windowActorMedia)
  const auth = useStore($auth)
  const owner = auth.kind === 'authenticated' ? auth.snapshot.sessionId : null
  const root = useRef<HTMLDivElement>(null)
  const first = useRef<HTMLVideoElement>(null)
  const second = useRef<HTMLVideoElement>(null)
  const layer = useRef<ReturnType<typeof createStageVideoLayer<WindowActorMedia>> | null>(null)
  const [displayed, setDisplayed] = useState<{ media: WindowActorMedia; video: HTMLVideoElement } | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const shown = displayed?.media.owner === owner ? displayed : null

  useLayoutEffect(() => {
    if (!first.current || !second.current) {
      return
    }

    const report = (
      value: WindowActorMedia,
      status: 'started' | 'finished' | 'failed' | 'interrupted',
      error?: string
    ): void => {
      if ($windowActorMedia.get()?.key === value.key && $windowActorMedia.get()?.owner === value.owner) {
        $windowActorPlayback.set({ key: value.key, status, error })
      }
    }

    const player = createStageVideoLayer<WindowActorMedia>([first.current, second.current], {
      presented: (source, video) => setDisplayed(source && video ? { media: source.value, video } : null),
      started: source => report(source.value, 'started'),
      ended: source => report(source.value, 'finished'),
      interrupted: source => report(source.value, 'interrupted'),
      failed: (source, error) => {
        log.warn('character-stage', 'Actor playback failed', error)
        report(source.value, 'failed', error)
      },
      frame: () => probeInteractiveRegions(windowId)
    })

    layer.current = player
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')

    const sync = (): void =>
      player.setPaused(!isCharacterMediaVisible() || query.matches || $presentation.get().fullscreen)

    const stopVisibility = observeCharacterStageVisibility(sync)
    query.addEventListener('change', sync)

    return () => {
      stopVisibility()
      query.removeEventListener('change', sync)
      player.dispose()
      layer.current = null
    }
  }, [windowId, owner])

  useLayoutEffect(() => {
    const wanted = media?.owner === owner ? media : null
    layer.current?.set(
      wanted
        ? {
            key: wanted.key,
            mediaKey: wanted.key.split(':').slice(0, 2).join(':'),
            loop: wanted.loop,
            natural: wanted.loop && wanted.key.split(':').length === 2,
            value: wanted,
            load: () => acquireStageVideo(wanted.url)
          }
        : null,
      wanted?.baseKey
    )
  }, [media, owner])

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

  useLayoutEffect(() => {
    if (!shown || size.width <= 0 || size.height <= 0) {
      return
    }

    const { video, media: value } = shown
    const width = video.videoWidth
    const height = video.videoHeight
    const bounds = value.body?.bodyBounds ?? [0, 0, width, height]
    const [left, top, right, bottom] = bounds
    const [safeLeft, safeTop, safeRight, safeBottom] = value.layout.safe_area
    const safeWidth = size.width * Math.max(0, safeRight - safeLeft)
    const safeHeight = size.height * Math.max(0, safeBottom - safeTop)
    const [positionX, positionY] = value.layout.actor_position

    const scale = contentAlign
      ? Math.min(size.width / Math.max(1, right - left), size.height / Math.max(1, bottom - top))
      : value.layout.fit === 'cover'
        ? Math.max(safeWidth / width, safeHeight / height)
        : Math.min(safeWidth / width, safeHeight / height)

    const offsetX =
      contentAlign === 'left'
        ? -left * scale
        : contentAlign === 'right'
          ? size.width - right * scale
          : size.width * safeLeft + safeWidth * positionX - width * scale * positionX

    const offsetY = contentAlign
      ? size.height - bottom * scale
      : size.height * safeTop + safeHeight * positionY - height * scale * positionY

    for (const element of [first.current, second.current]) {
      if (element) {
        Object.assign(element.style, {
          left: `${offsetX}px`,
          top: `${offsetY}px`,
          width: `${width * scale}px`,
          height: `${height * scale}px`
        })
      }
    }

    const rect = {
      left: (offsetX + left * scale) / size.width,
      top: (offsetY + top * scale) / size.height,
      right: (offsetX + right * scale) / size.width,
      bottom: (offsetY + bottom * scale) / size.height
    }

    if (!contentAlign) {
      $spriteCanvasRect.set({
        left: offsetX / size.width,
        top: offsetY / size.height,
        right: (offsetX + width * scale) / size.width,
        bottom: (offsetY + height * scale) / size.height
      })
      $spriteContentRect.set(rect)
      const head = value.body?.headAnchor
      const headY = head ? (offsetY + head[1] * scale) / size.height : rect.top + (rect.bottom - rect.top) * 0.09
      const headX = head ? (offsetX + head[0] * scale) / size.width : (rect.left + rect.right) / 2
      $spriteHeadRect.set({
        left: Math.max(rect.left, headX - (rect.right - rect.left) * 0.12),
        right: Math.min(rect.right, headX + (rect.right - rect.left) * 0.12),
        top: Math.max(rect.top, headY - (rect.bottom - rect.top) * 0.09),
        bottom: Math.min(rect.bottom, headY + (rect.bottom - rect.top) * 0.09)
      })
      const foot = value.body?.footAnchor
      $spriteFootAnchor.set(
        foot ? { x: (offsetX + foot[0] * scale) / size.width, y: (offsetY + foot[1] * scale) / size.height } : null
      )
    }

    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(width * Math.min(1, 256 / Math.max(width, height))))
    canvas.height = Math.max(1, Math.round(height * Math.min(1, 256 / Math.max(width, height))))
    const context = canvas.getContext('2d', { willReadFrequently: true })
    let sampledTime = -1
    let pixels: Uint8ClampedArray | null = null
    $mediaHitTest.set((x, y) => {
      const box = video.getBoundingClientRect()
      const nx = (x - box.left) / box.width
      const ny = (y - box.top) / box.height

      if (nx < 0 || nx >= 1 || ny < 0 || ny >= 1 || !context) {
        return false
      }

      try {
        if (sampledTime !== video.currentTime || !pixels) {
          context.drawImage(video, 0, 0, canvas.width, canvas.height)
          pixels = context.getImageData(0, 0, canvas.width, canvas.height).data
          sampledTime = video.currentTime
        }

        return (
          (pixels[(Math.floor(ny * canvas.height) * canvas.width + Math.floor(nx * canvas.width)) * 4 + 3] ?? 0) >= 16
        )
      } catch {
        return false
      }
    })
    probeInteractiveRegions(windowId)

    return () => {
      $mediaHitTest.set(null)

      if (!contentAlign) {
        $spriteCanvasRect.set(null)
        $spriteContentRect.set(null)
        $spriteHeadRect.set(null)
        $spriteFootAnchor.set(null)
      }
    }
  }, [shown, size, contentAlign, windowId])

  const videoStyle: React.CSSProperties = {
    position: 'absolute',
    inset: 0,
    width: '100%',
    height: '100%',
    objectFit: 'contain',
    opacity: 0,
    pointerEvents: 'none'
  }

  return (
    <div className="relative h-full w-full overflow-hidden" ref={root}>
      <div className="absolute inset-0" style={{ visibility: shown ? 'hidden' : 'visible' }}>
        <OutfitStage active={!shown} contentAlign={contentAlign} eggSize={eggSize} windowId={windowId} />
      </div>
      <video muted playsInline preload="auto" ref={first} style={videoStyle} />
      <video muted playsInline preload="auto" ref={second} style={videoStyle} />
    </div>
  )
}
