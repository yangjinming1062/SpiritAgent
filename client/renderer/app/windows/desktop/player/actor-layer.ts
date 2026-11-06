import { createMediaObjectUrl } from '@/features/media'
import { createStageVideoLayer } from '@/shared/lib/stage-video-layer'
import { DEFAULT_STAGE_LAYOUT } from '@ipc/contracts'
import type { DesktopBackground, DesktopBackgroundPlayback, DesktopStageLayout } from '@ipc/contracts'

import type { ReceiptLog } from './receipt'

interface ActorLayerOptions {
  first: HTMLVideoElement
  second: HTMLVideoElement
  poster: HTMLImageElement
  container: HTMLDivElement
  receipts: ReceiptLog
  isDisposed: () => boolean
  isPaused: () => boolean
  applyLayerOrder: (layout: DesktopStageLayout) => void
  onPresented: () => void
  onPosterReady: () => void
  onDiscardWaiting: () => void
}

export interface ActorLayer {
  allow: (value: boolean) => void
  hasPoster: () => boolean
  isVisible: () => boolean
  layout: () => void
  receive: (media: DesktopBackground) => void
  reset: () => void
  dispose: () => void
}

/** 主体层：透明视频双缓冲 + 造型静帧兜底；环境层换图不打断这里。 */
export function createActorLayer({
  first,
  second,
  poster,
  container,
  receipts,
  isDisposed,
  isPaused,
  applyLayerOrder,
  onPresented,
  onPosterReady,
  onDiscardWaiting
}: ActorLayerOptions): ActorLayer {
  let visible = false
  let allowed = false
  let posterUrl: string | null = null
  let posterKey: string | null = null
  let posterGeneration = 0
  let element: HTMLVideoElement | null = null
  let layout: DesktopStageLayout = DEFAULT_STAGE_LAYOUT

  const acknowledge = (media: DesktopBackground, status: DesktopBackgroundPlayback['status'], error?: string): void => {
    if (isDisposed() || media.playTarget !== 'actor' || !media.playId || receipts.has(media.playId)) {
      return
    }

    receipts.send(media, status, error ? error.slice(0, 2000) : undefined, 'Actor acknowledgment failed')
  }

  const layoutElement = (): void => {
    const target = element

    if (!target || !target.videoWidth || !target.videoHeight) {
      return
    }

    const [safeLeft, safeTop, safeRight, safeBottom] = layout.safeArea
    const areaWidth = window.innerWidth * (safeRight - safeLeft)
    const areaHeight = window.innerHeight * (safeBottom - safeTop)

    const scale =
      layout.fit === 'cover'
        ? Math.max(areaWidth / target.videoWidth, areaHeight / target.videoHeight)
        : Math.min(areaWidth / target.videoWidth, areaHeight / target.videoHeight)

    const [positionX, positionY] = layout.actorPosition

    Object.assign(target.style, {
      left: `${window.innerWidth * safeLeft + areaWidth * positionX - target.videoWidth * scale * positionX}px`,
      top: `${window.innerHeight * safeTop + areaHeight * positionY - target.videoHeight * scale * positionY}px`,
      width: `${target.videoWidth * scale}px`,
      height: `${target.videoHeight * scale}px`
    })
  }

  const layer = createStageVideoLayer<DesktopBackground>([first, second], {
    presented: (_source, next) => {
      visible = next !== null
      poster.style.opacity = visible ? '0' : '1'
      element = next
      layoutElement()

      if (visible) {
        onPresented()
      }
    },
    started: source => acknowledge(source.value, 'started'),
    ended: source => acknowledge(source.value, 'completed'),
    failed: (source, error) => acknowledge(source.value, 'failed', error),
    interrupted: source => acknowledge(source.value, 'interrupted')
  })

  const allow = (value: boolean): void => {
    allowed = value
    container.style.opacity = value ? '1' : '0'
    layer.setPaused(isPaused() || !value)
  }

  const reset = (): void => {
    // 等待主体层首帧的背景回执不能静默丢弃：主体被清时按失败结算，避免账本悬挂到过期兜底。
    onDiscardWaiting()
    layer.set(null)
    posterGeneration += 1

    if (posterUrl) {
      URL.revokeObjectURL(posterUrl)
    }

    posterUrl = null
    posterKey = null
    layout = DEFAULT_STAGE_LAYOUT
    applyLayerOrder(layout)
    poster.removeAttribute('src')
    allow(false)
  }

  const receive = (media: DesktopBackground): void => {
    layer.setPaused(isPaused() || !allowed)

    if (!media.actor && !(media.backgroundKind === 'scene' && media.outfit)) {
      reset()

      return
    }

    const mediaKey = media.actor ? `${media.actorVisualId}:${media.actorVisualVersion}` : `static:${media.revision}`
    layout = media.layout
    applyLayerOrder(layout)
    const key = `${mediaKey}${media.playTarget === 'actor' && media.playId ? `:${media.playId}` : ''}`
    const actorBytes = media.actor
    layer.set(
      actorBytes
        ? {
            key,
            mediaKey,
            loop: media.actorKind === 'loop',
            natural: media.playTarget !== 'actor' || media.playId === null,
            value: media,
            load: async () => {
              const url = createMediaObjectUrl(actorBytes)

              return { url, release: () => URL.revokeObjectURL(url) }
            }
          }
        : null,
      media.actorBaseKey
    )
    const outfit = media.outfit

    if (outfit && posterKey !== mediaKey) {
      posterKey = mediaKey
      const generation = ++posterGeneration
      const url = createMediaObjectUrl(outfit)
      const image = new Image()
      image.src = url
      void image
        .decode()
        .then(() => {
          if (isDisposed() || generation !== posterGeneration) {
            URL.revokeObjectURL(url)

            return
          }

          if (posterUrl) {
            URL.revokeObjectURL(posterUrl)
          }

          posterUrl = url
          poster.src = url
          poster.style.opacity = visible ? '0' : '1'
          onPosterReady()
        })
        .catch(error => {
          URL.revokeObjectURL(url)
          console.warn('Actor fallback failed', error)
        })
    }
  }

  return {
    allow,
    hasPoster: () => posterUrl !== null,
    isVisible: () => visible,
    layout: layoutElement,
    receive,
    reset,
    dispose: () => {
      posterGeneration += 1

      if (posterUrl) {
        URL.revokeObjectURL(posterUrl)
        posterUrl = null
      }

      layer.dispose()
    }
  }
}
