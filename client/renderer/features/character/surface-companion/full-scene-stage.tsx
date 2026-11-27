import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useLayoutEffect, useRef, useState } from 'react'

import { RENDERER_TIMINGS } from '@/shared/constants'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { acquireStageVideo } from '@/shared/lib/stage-video-cache'
import { createStageVideoLayer } from '@/shared/lib/stage-video-layer'
import { backendApi } from '@/shared/platform/desktop'
import { $lifeStageMedia, type LifeStageMedia } from '@/shared/store/window-stage'
import { useStrings } from '@/shared/strings'
import { BTN_SUBTLE } from '@/ui'
import { desktopMediaLayout } from '@client-shared/desktop-media-geometry'

import styles from './surface-companion.module.css'

interface DisplayedFrame<Element extends HTMLImageElement | HTMLVideoElement> {
  source: LifeStageMedia
  element: Element
}

function frameDimensions(element: HTMLImageElement | HTMLVideoElement): [number, number] {
  return element instanceof HTMLVideoElement
    ? [element.videoWidth, element.videoHeight]
    : [element.naturalWidth, element.naturalHeight]
}

function sameEnvironment(previous: LifeStageMedia, current: LifeStageMedia): boolean {
  return (
    previous.owner === current.owner &&
    previous.presentationEpoch === current.presentationEpoch &&
    current.sceneHash !== null &&
    previous.sceneHash === current.sceneHash
  )
}

function isSelected(source: LifeStageMedia): boolean {
  const current = $lifeStageMedia.get()

  return current?.key === source.key && current.owner === source.owner
}

export function FullSceneStage({ owner }: { owner: string }): React.JSX.Element {
  const selected = useStore($lifeStageMedia)
  const target = selected?.owner === owner ? selected : null
  const [showImage, setShowImage] = useState(false)
  const [showVideo, setShowVideo] = useState(false)
  const [failedKey, setFailedKey] = useState<string | null>(null)
  const [retry, setRetry] = useState<{ key: string } | null>(null)
  const firstVideo = useRef<HTMLVideoElement>(null)
  const secondVideo = useRef<HTMLVideoElement>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const backdrop = useRef<HTMLCanvasElement>(null)
  const imageHost = useRef<HTMLDivElement>(null)
  const image = useRef<DisplayedFrame<HTMLImageElement> | null>(null)
  const player = useRef<ReturnType<typeof createStageVideoLayer<LifeStageMedia>> | null>(null)
  const activeVideo = useRef<DisplayedFrame<HTMLVideoElement> | null>(null)
  const isEmpty = useCallback(() => !image.current && !activeVideo.current, [])
  const strings = useStrings()
  const failed = target?.key === failedKey

  const canRetry =
    failed && target?.kind === 'image' && target.mediaKey === target.key && target.key.startsWith('render:complete:')

  const drawBackdrop = useCallback((element: HTMLImageElement | HTMLVideoElement): void => {
    const canvas = backdrop.current

    if (!canvas || canvas.hidden || (element instanceof HTMLVideoElement && element.readyState < 2)) {
      return
    }

    const [width, height] = frameDimensions(element)

    if (!width || !height) {
      return
    }

    // 铺底只需很小的采样，直接使用正在展示的帧，不启动第二路解码。
    const scale = Math.min(1, 144 / Math.max(width, height))
    const sampleWidth = Math.max(1, Math.round(width * scale))
    const sampleHeight = Math.max(1, Math.round(height * scale))

    if (canvas.width !== sampleWidth || canvas.height !== sampleHeight) {
      canvas.width = sampleWidth
      canvas.height = sampleHeight
    }

    canvas.getContext('2d')?.drawImage(element, 0, 0, sampleWidth, sampleHeight)
  }, [])

  const syncLayout = useCallback((): void => {
    const canvas = backdrop.current
    const container = viewport.current
    const element = activeVideo.current?.element ?? image.current?.element

    if (!canvas || !container) {
      return
    }

    if (!element || !container.clientWidth || !container.clientHeight) {
      canvas.hidden = true

      return
    }

    const [width, height] = frameDimensions(element)
    const { objectFit } = desktopMediaLayout(width, height, container.clientWidth, container.clientHeight)

    element.style.objectFit = objectFit
    canvas.hidden = objectFit === 'cover' || !width || !height
    drawBackdrop(element)
  }, [drawBackdrop])

  const clearImage = useCallback((): void => {
    image.current?.element.remove()
    image.current?.element.removeAttribute('src')
    image.current = null
    setShowImage(false)
    syncLayout()
  }, [syncLayout])

  const rejectFrame = useCallback(
    (source: LifeStageMedia, error?: string): void => {
      if (!isSelected(source)) {
        return
      }

      const displayedImage = image.current?.source
      const displayedVideo = activeVideo.current?.source

      if (displayedImage && !sameEnvironment(displayedImage, source)) {
        clearImage()
      }

      if (displayedVideo && !sameEnvironment(displayedVideo, source)) {
        player.current?.set(null)
      }

      if (isEmpty()) {
        source.cleared(isEmpty)
      }

      if (error) {
        setFailedKey(source.key)
        source.playback('failed', error)
      } else {
        source.playback('interrupted')
      }
    },
    [clearImage, isEmpty]
  )

  useLayoutEffect(() => {
    const first = firstVideo.current
    const second = secondVideo.current
    const container = viewport.current

    if (!first || !second || !container) {
      return
    }

    const layer = createStageVideoLayer<LifeStageMedia>([first, second], {
      presented: (source, element) => {
        activeVideo.current = source && element ? { source: source.value, element } : null

        if (image.current) {
          image.current.element.hidden = Boolean(source)
        }

        setShowVideo(Boolean(source))
        syncLayout()
      },
      started: source => {
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            if (activeVideo.current?.source.key === source.key) {
              source.value.playback('started')
            }
          })
        )
      },
      ended: source => {
        syncLayout()
        source.value.playback('finished')
      },
      failed: (source, error) => rejectFrame(source.value, error),
      interrupted: source => source.value.playback('interrupted'),
      frame: drawBackdrop
    })

    const observer = new ResizeObserver(syncLayout)
    observer.observe(container)
    first.addEventListener('resize', syncLayout)
    second.addEventListener('resize', syncLayout)
    player.current = layer
    layer.setPaused(false)

    return () => {
      observer.disconnect()
      first.removeEventListener('resize', syncLayout)
      second.removeEventListener('resize', syncLayout)
      activeVideo.current = null
      image.current?.element.removeAttribute('src')
      image.current?.element.remove()
      image.current = null
      player.current = null
      layer.dispose()
    }
  }, [drawBackdrop, rejectFrame, syncLayout])

  useLayoutEffect(() => {
    const layer = player.current

    if (!target) {
      const displayed = activeVideo.current?.source ?? image.current?.source
      layer?.set(null)
      clearImage()
      displayed?.cleared(isEmpty)

      return
    }

    const scope = captureAuthScope()

    if (target.kind === 'video') {
      const currentVideo = activeVideo.current?.source
      const fallback = currentVideo?.loop && sameEnvironment(currentVideo, target) ? currentVideo.mediaKey : null

      layer?.set(
        {
          key: target.key,
          mediaKey: target.mediaKey,
          loop: target.loop,
          natural: target.natural,
          holdLastFrame: true,
          expiresAt: target.expiresAt,
          canActivate: () => Boolean(scope?.() && isSelected(target)),
          value: target,
          load: () => acquireStageVideo(target.url)
        },
        fallback
      )
      layer?.setPaused(false)

      return
    }

    layer?.cancelPending()
    layer?.setPaused(true)
    syncLayout()
    const candidate = new Image()
    candidate.alt = ''
    candidate.draggable = false
    candidate.className = styles.frame
    let active = true
    let presented = false
    let paint: number | undefined

    const current = (): boolean => Boolean(active && scope?.() && isSelected(target))
    const expiresIn = target.expiresAt === null ? Infinity : target.expiresAt - Date.now()

    if (expiresIn <= 0) {
      rejectFrame(target)

      return
    }

    const timeout = setTimeout(
      () => fail(expiresIn <= RENDERER_TIMINGS.sceneImageTimeoutMs ? undefined : 'Image first frame timed out'),
      Math.min(expiresIn, RENDERER_TIMINGS.sceneImageTimeoutMs)
    )

    function fail(error?: string): void {
      if (target && current()) {
        active = false
        clearTimeout(timeout)
        candidate.removeAttribute('src')
        rejectFrame(target, error)
      }
    }

    void backendApi()
      .apiAsset({ url: target.url, preferCache: retry?.key !== target.key })
      .then(async url => {
        if (!current()) {
          return
        }

        if (!url) {
          throw new Error('Stage image is unavailable')
        }

        candidate.src = url
        await candidate.decode()

        if (!current()) {
          return
        }

        paint = requestAnimationFrame(() => {
          if (!current()) {
            return
          }

          if (target.expiresAt !== null && target.expiresAt <= Date.now()) {
            fail()

            return
          }

          const host = imageHost.current

          if (!host) {
            return
          }

          // 将已解码、仍在有效期内的图片挂到舞台上，再退出视频。
          clearTimeout(timeout)
          image.current?.element.removeAttribute('src')
          host.replaceChildren(candidate)
          image.current = { source: target, element: candidate }
          presented = true
          layer?.set(null)
          setShowImage(true)

          paint = requestAnimationFrame(() => {
            if (current() && image.current?.element === candidate) {
              target.playback('started')
            }
          })
        })
      })
      .catch(error => fail(String(error)))

    return () => {
      active = false
      clearTimeout(timeout)

      if (paint !== undefined) {
        cancelAnimationFrame(paint)
      }

      if (!presented) {
        candidate.removeAttribute('src')
      }
    }
  }, [target, retry, clearImage, isEmpty, rejectFrame, syncLayout])

  return (
    <>
      <div aria-hidden="true" className={styles.mediaViewport} ref={viewport}>
        <canvas className={styles.backdrop} hidden ref={backdrop} />
        <div className={styles.imageHost} ref={imageHost} />
        <video className={styles.video} muted playsInline preload="auto" ref={firstVideo} />
        <video className={styles.video} muted playsInline preload="auto" ref={secondVideo} />
      </div>
      {((!showVideo && !showImage) || canRetry) && (
        <div className={styles.waiting}>
          <p>{failed ? strings.creation.previewFailed : strings.visuals.emptyTitle}</p>
          {canRetry && (
            <button
              className={`${BTN_SUBTLE} ${styles.retry}`}
              onClick={() => {
                if (
                  !captureAuthScope()?.() ||
                  !isSelected(target) ||
                  (target.expiresAt !== null && target.expiresAt <= Date.now())
                ) {
                  return
                }

                setFailedKey(null)
                setRetry({ key: target.key })
              }}
              type="button"
            >
              {strings.creation.retryLoad}
            </button>
          )}
        </div>
      )}
    </>
  )
}
