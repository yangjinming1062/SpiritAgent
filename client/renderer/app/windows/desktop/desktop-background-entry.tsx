import '../../../styles.css'

import { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'

import { ambientPhaseFilter } from '@/shared/lib/day-phase'
import { createStageVideoLayer } from '@/shared/lib/stage-video-layer'
import { backgroundMedia } from '@/shared/platform/desktop'
import { desktopMediaLayout } from '@client-shared/desktop-media-geometry'
import { DEFAULT_STAGE_LAYOUT } from '@ipc/contracts'
import type {
  DesktopBackground,
  DesktopBackgroundPlayback,
  DesktopStageLayer,
  DesktopStageLayout
} from '@ipc/contracts'

import { prepareStaticPoster } from './static-poster'

interface VideoSlot {
  element: HTMLVideoElement
  media: DesktopBackground | null
  videoUrl: string | null
  posterUrl: string | null
  ready: boolean
  started: boolean
  terminal: boolean
  timeout: ReturnType<typeof setTimeout> | null
  frameCallback: number | null
  generation: number
}

const FALLBACK_BACKGROUND = 'radial-gradient(circle at center, var(--ui-fill-hover), var(--ui-app-bg) 72%)'

// 壁纸表面只消费主进程取得的账户媒体字节，没有鉴权、API 或会话能力。
function DesktopWallpaper() {
  const first = useRef<HTMLVideoElement>(null)
  const second = useRef<HTMLVideoElement>(null)
  const backdrop = useRef<HTMLDivElement>(null)
  const poster = useRef<HTMLImageElement>(null)
  const transitionFrame = useRef<HTMLCanvasElement>(null)
  const actorFirst = useRef<HTMLVideoElement>(null)
  const actorSecond = useRef<HTMLVideoElement>(null)
  const actorPoster = useRef<HTMLImageElement>(null)
  const actorRoot = useRef<HTMLDivElement>(null)
  const phaseMotionEnabled = useRef(true)

  useEffect(() => {
    const bridge = backgroundMedia()
    const firstVideo = first.current
    const secondVideo = second.current
    const fill = backdrop.current
    const still = poster.current
    const transition = transitionFrame.current

    if (
      !bridge ||
      !firstVideo ||
      !secondVideo ||
      !fill ||
      !still ||
      !transition ||
      !actorFirst.current ||
      !actorSecond.current ||
      !actorPoster.current ||
      !actorRoot.current
    ) {
      throw new Error('Desktop background bridge is unavailable')
    }

    const slots: VideoSlot[] = [firstVideo, secondVideo].map(element => ({
      element,
      media: null,
      videoUrl: null,
      posterUrl: null,
      ready: false,
      started: false,
      terminal: false,
      timeout: null,
      frameCallback: null,
      generation: 0
    }))

    let active: number | null = null
    let retainedLoop: number | null = null
    let pending: number | null = null
    let returningLoop: number | null = null
    let returningFrom: number | null = null
    let notifyReturnFrame = false
    let deferredMedia: DesktopBackground | null = null
    let accountEpoch = -1
    let revision = -1
    let paused = false
    let fallbackPoster: string | null = null
    let fillPosterUrl: string | null = null
    let disposed = false
    let imageGeneration = 0
    let staticMedia: DesktopBackground | null = null
    let fadeTimer: ReturnType<typeof setTimeout> | null = null
    let fadeFrame: number | null = null
    const settled = new Set<string>()

    const actorStill = actorPoster.current
    const actorContainer = actorRoot.current
    let actorVisible = false
    let actorAllowed = false
    let actorPosterUrl: string | null = null
    let actorPosterKey: string | null = null
    let actorPosterGeneration = 0
    let waitingImage: DesktopBackground | null = null
    let actorElement: HTMLVideoElement | null = null

    let actorLayout: DesktopStageLayout = DEFAULT_STAGE_LAYOUT

    const layerZ = (layer: DesktopStageLayer): string => {
      const index = actorLayout.layerOrder.indexOf(layer)

      return String(index >= 0 ? index + 1 : actorLayout.layerOrder.length + 1)
    }

    const applyLayerOrder = (): void => {
      actorContainer.style.zIndex = layerZ('actor')
      actorStill.style.zIndex = layerZ('actor')
      still.style.zIndex = layerZ('scene')
      transition.style.zIndex = layerZ('effects')
    }

    const layoutActor = (): void => {
      const element = actorElement

      if (!element || !element.videoWidth || !element.videoHeight) {
        return
      }

      const [safeLeft, safeTop, safeRight, safeBottom] = actorLayout.safeArea
      const areaWidth = window.innerWidth * (safeRight - safeLeft)
      const areaHeight = window.innerHeight * (safeBottom - safeTop)

      const scale =
        actorLayout.fit === 'cover'
          ? Math.max(areaWidth / element.videoWidth, areaHeight / element.videoHeight)
          : Math.min(areaWidth / element.videoWidth, areaHeight / element.videoHeight)

      const [positionX, positionY] = actorLayout.actorPosition

      Object.assign(element.style, {
        left: `${window.innerWidth * safeLeft + areaWidth * positionX - element.videoWidth * scale * positionX}px`,
        top: `${window.innerHeight * safeTop + areaHeight * positionY - element.videoHeight * scale * positionY}px`,
        width: `${element.videoWidth * scale}px`,
        height: `${element.videoHeight * scale}px`
      })
    }

    const acknowledgeActor = (
      media: DesktopBackground,
      status: DesktopBackgroundPlayback['status'],
      error?: string
    ): void => {
      if (disposed || media.playTarget !== 'actor' || !media.playId || settled.has(media.playId)) {
        return
      }

      if (status === 'completed' || status === 'failed' || status === 'interrupted') {
        settled.add(media.playId)
      }

      void bridge
        .acknowledge({
          accountEpoch: media.accountEpoch,
          revision: media.revision,
          playId: media.playId,
          status,
          ...(error ? { error: error.slice(0, 2000) } : {})
        })
        .catch(failure => console.warn('Actor acknowledgment failed', failure))
    }

    const actorLayer = createStageVideoLayer<DesktopBackground>([actorFirst.current, actorSecond.current], {
      presented: (_source, element) => {
        actorVisible = element !== null
        actorStill.style.opacity = actorVisible ? '0' : '1'
        actorElement = element
        layoutActor()

        if (waitingImage && actorVisible) {
          const media = waitingImage
          waitingImage = null
          acknowledgeImage(media, 'started')
        }
      },
      started: source => acknowledgeActor(source.value, 'started'),
      ended: source => acknowledgeActor(source.value, 'completed'),
      failed: (source, error) => acknowledgeActor(source.value, 'failed', error),
      interrupted: source => acknowledgeActor(source.value, 'interrupted')
    })

    const allowActor = (value: boolean): void => {
      actorAllowed = value
      actorContainer.style.opacity = value ? '1' : '0'
      actorLayer.setPaused(paused || !value)
    }

    const resetActor = (): void => {
      // 等待主体层首帧的背景回执不能静默丢弃：主体被清时按失败结算，避免账本悬挂到过期兜底。
      if (waitingImage) {
        const pending = waitingImage
        waitingImage = null
        acknowledgeImage(pending, 'failed', 'Actor layer removed before display')
      }

      actorLayer.set(null)
      actorPosterGeneration += 1

      if (actorPosterUrl) {
        URL.revokeObjectURL(actorPosterUrl)
      }

      actorPosterUrl = null
      actorPosterKey = null
      actorLayout = DEFAULT_STAGE_LAYOUT
      applyLayerOrder()
      actorStill.removeAttribute('src')
      allowActor(false)
    }

    const receiveActor = (media: DesktopBackground): void => {
      actorLayer.setPaused(paused || !actorAllowed)

      if (!media.actor) {
        resetActor()

        return
      }

      const mediaKey = `${media.actorVisualId}:${media.actorVisualVersion}`
      actorLayout = media.layout
      applyLayerOrder()
      const key = `${mediaKey}${media.playTarget === 'actor' && media.playId ? `:${media.playId}` : ''}`
      const actorBytes = media.actor
      actorLayer.set(
        {
          key,
          mediaKey,
          loop: media.actorKind === 'loop',
          value: media,
          load: async () => {
            const url = URL.createObjectURL(new Blob([new Uint8Array(actorBytes.bytes)], { type: actorBytes.mime }))

            return { url, release: () => URL.revokeObjectURL(url) }
          }
        },
        media.actorBaseKey
      )
      const outfit = media.outfit

      if (outfit && actorPosterKey !== mediaKey) {
        actorPosterKey = mediaKey
        const generation = ++actorPosterGeneration
        const url = URL.createObjectURL(new Blob([new Uint8Array(outfit.bytes)], { type: outfit.mime }))
        const image = new Image()
        image.src = url
        void image
          .decode()
          .then(() => {
            if (disposed || generation !== actorPosterGeneration) {
              URL.revokeObjectURL(url)

              return
            }

            if (actorPosterUrl) {
              URL.revokeObjectURL(actorPosterUrl)
            }

            actorPosterUrl = url
            actorStill.src = url
            actorStill.style.opacity = actorVisible ? '0' : '1'

            if (waitingImage) {
              const pendingImage = waitingImage
              waitingImage = null
              acknowledgeImage(pendingImage, 'started')
            }
          })
          .catch(error => {
            URL.revokeObjectURL(url)
            console.warn('Actor fallback failed', error)
          })
      }
    }

    const acknowledgeImage = (
      media: DesktopBackground,
      status: DesktopBackgroundPlayback['status'],
      error?: string
    ): void => {
      if (status === 'started' && media.actor && !actorVisible && !actorPosterUrl) {
        waitingImage = media

        return
      }

      if (!media.playId || disposed || settled.has(media.playId)) {
        return
      }

      if (status !== 'started' && status !== 'first-frame') {
        settled.add(media.playId)
      }

      void bridge
        .acknowledge({
          accountEpoch: media.accountEpoch,
          revision: media.revision,
          playId: media.playId,
          status,
          ...(error ? { error } : {})
        })
        .catch(failure => console.warn('Image acknowledgment failed', failure))
    }

    const setFill = (url: string | null): void => {
      fillPosterUrl = url
      fill.style.backgroundImage = url ? `url("${url}")` : FALLBACK_BACKGROUND
    }

    const acknowledge = (slot: VideoSlot, status: DesktopBackgroundPlayback['status'], error?: string): void => {
      const media = slot.media

      if (!media?.playId || disposed) {
        return
      }

      if (status === 'completed' || status === 'failed' || status === 'interrupted') {
        if (slot.terminal) {
          return
        }

        slot.terminal = true
        settled.add(media.playId)

        if (settled.size > 64) {
          const oldest = settled.values().next().value

          if (oldest) {
            settled.delete(oldest)
          }
        }
      }

      void bridge
        .acknowledge({
          accountEpoch: media.accountEpoch,
          revision: media.revision,
          playId: media.playId,
          status,
          ...(error ? { error } : {})
        })
        .catch(failure => console.warn('Desktop background acknowledgment failed', failure))
    }

    const interruptDeferred = (): void => {
      if (!deferredMedia?.playId || disposed) {
        return
      }

      settled.add(deferredMedia.playId)
      void bridge
        .acknowledge({
          accountEpoch: deferredMedia.accountEpoch,
          revision: deferredMedia.revision,
          playId: deferredMedia.playId,
          status: 'interrupted'
        })
        .catch(failure => console.warn('Desktop background acknowledgment failed', failure))
    }

    const reset = (slot: VideoSlot): void => {
      slot.generation += 1

      if (slot.frameCallback !== null) {
        slot.element.cancelVideoFrameCallback(slot.frameCallback)
        slot.frameCallback = null
      }

      if (pending !== null && slots[pending] === slot) {
        pending = null

        if (active !== null && slots[active].media?.kind === 'loop') {
          slots[active].element.loop = slots[active].media?.transition !== 'fade'
        }
      }

      if (slot.timeout) {
        clearTimeout(slot.timeout)
      }

      slot.timeout = null
      slot.element.pause()
      slot.element.removeAttribute('src')
      slot.element.removeAttribute('poster')
      slot.element.load()
      slot.element.style.opacity = '0'

      if (slot.videoUrl) {
        URL.revokeObjectURL(slot.videoUrl)
      }

      if (slot.posterUrl) {
        const revokedPoster = slot.posterUrl

        const replacement =
          slots.find(candidate => candidate !== slot && candidate.posterUrl)?.posterUrl ?? fallbackPoster ?? null

        if (fillPosterUrl === revokedPoster) {
          setFill(replacement)
        }

        URL.revokeObjectURL(revokedPoster)
      }

      slot.media = null
      slot.videoUrl = null
      slot.posterUrl = null
      slot.ready = false
      slot.started = false
      slot.terminal = false
    }

    const updateMediaLayout = (
      element: HTMLImageElement | HTMLVideoElement,
      width: number,
      height: number,
      stageLayout: DesktopStageLayout
    ): void => {
      const [safeLeft, safeTop, safeRight, safeBottom] = stageLayout.safeArea
      const areaWidth = window.innerWidth * (safeRight - safeLeft)
      const areaHeight = window.innerHeight * (safeBottom - safeTop)

      const computed = desktopMediaLayout(width, height, areaWidth, areaHeight)
      const areaLeft = window.innerWidth * safeLeft
      const areaTop = window.innerHeight * safeTop

      const layout =
        stageLayout.fit === 'cover'
          ? { objectFit: 'cover' as const, left: areaLeft, top: areaTop, width: areaWidth, height: areaHeight }
          : {
              ...computed,
              left: computed.left + areaLeft,
              top: computed.top + areaTop
            }

      // 完整显示时媒体元素仅占实际内容区域，周围由模糊底层填充。
      Object.assign(element.style, {
        left: `${layout.left}px`,
        top: `${layout.top}px`,
        width: `${layout.width}px`,
        height: `${layout.height}px`,
        objectFit: layout.objectFit
      })
    }

    const updatePosterLayout = (): void =>
      updateMediaLayout(still, still.naturalWidth, still.naturalHeight, staticMedia?.layout ?? actorLayout)

    const showPoster = (url: string | null): void => {
      if (url) {
        setFill(url)
      } else if (!fillPosterUrl) {
        setFill(null)
      }

      if (url && active === null) {
        still.src = url
        updatePosterLayout()
        still.style.opacity = '1'
      } else {
        still.style.opacity = '0'
      }
    }

    const updateVideoLayout = (slot: VideoSlot): void =>
      updateMediaLayout(
        slot.element,
        slot.element.videoWidth,
        slot.element.videoHeight,
        slot.media?.layout ?? actorLayout
      )

    const updateAllMediaLayouts = (): void => {
      slots.forEach(updateVideoLayout)
      updatePosterLayout()
      layoutActor()
    }

    const start = (slot: VideoSlot): void => {
      if (paused || !slot.ready || slot.terminal) {
        slot.element.pause()

        return
      }

      if (!slot.started && slot.media?.expiresAt && Date.parse(slot.media.expiresAt) <= Date.now()) {
        acknowledge(slot, 'failed', 'Playback request expired before playback started')

        return
      }

      const generation = slot.generation

      void slot.element.play().catch(error => {
        if (!paused && slot.generation === generation && slot.ready && !slot.terminal) {
          acknowledge(slot, 'failed', String(error))
        }
      })
    }

    const captureTransition = (slot: VideoSlot): void => {
      if (paused || !slot.ready || !slot.element.videoWidth) {
        return
      }

      const context = transition.getContext('2d')

      if (!context) {
        return
      }

      if (fadeTimer) {
        clearTimeout(fadeTimer)
      }

      if (fadeFrame !== null) {
        cancelAnimationFrame(fadeFrame)
      }

      transition.width = window.innerWidth
      transition.height = window.innerHeight
      const fit = slot.element.style.objectFit

      const scale = (fit === 'cover' ? Math.max : Math.min)(
        transition.width / slot.element.videoWidth,
        transition.height / slot.element.videoHeight
      )

      const width = slot.element.videoWidth * scale
      const height = slot.element.videoHeight * scale
      context.clearRect(0, 0, transition.width, transition.height)
      context.drawImage(slot.element, (transition.width - width) / 2, (transition.height - height) / 2, width, height)
      transition.style.transition = 'none'
      transition.style.opacity = '1'
    }

    const fadeTransition = (): void => {
      fadeFrame = requestAnimationFrame(() => {
        fadeFrame = requestAnimationFrame(() => {
          fadeFrame = null
          transition.style.transition = 'opacity 180ms ease'
          transition.style.opacity = '0'
          fadeTimer = setTimeout(() => {
            fadeTimer = null
          }, 180)
        })
      })
    }

    const show = (index: number): void => {
      const slot = slots[index]
      const previous = active === null ? null : slots[active]

      if (slot.media?.kind === 'loop' && staticMedia) {
        acknowledgeImage(staticMedia, 'interrupted')
        staticMedia = null
      }

      if (
        previous &&
        previous !== slot &&
        (previous.media?.transition === 'fade' ||
          slot.media?.transition === 'fade' ||
          !(sharesBase(previous.media, slot.media!) || sharesBase(slot.media, previous.media!)))
      ) {
        captureTransition(previous)
        fadeTransition()
      }

      active = index
      allowActor(Boolean(slot.media?.actor))
      slot.element.style.opacity = '1'
      slot.element.style.zIndex = layerZ(slot.media?.actor ? 'scene' : 'cinematic')
      still.style.opacity = '0'
      showPoster(slot.posterUrl)

      for (let i = 0; i < slots.length; i += 1) {
        if (i !== index) {
          slots[i].element.style.opacity = '0'
          slots[i].element.style.zIndex = '0'
          slots[i].element.pause()
        }
      }

      start(slot)
    }

    const waitForFrame = (slot: VideoSlot, ready: () => void): void => {
      if (slot.frameCallback !== null) {
        slot.element.cancelVideoFrameCallback(slot.frameCallback)
      }

      const generation = slot.generation
      slot.frameCallback = slot.element.requestVideoFrameCallback(() => {
        slot.frameCallback = null

        if (slot.generation === generation && !disposed) {
          ready()
        }
      })
    }

    const finishReturn = (index: number): void => {
      const slot = slots[index]

      if (returningLoop !== index || !slot.ready || slot.terminal) {
        return
      }

      const previous = returningFrom
      returningLoop = null
      returningFrom = null

      if (notifyReturnFrame) {
        acknowledge(slot, 'first-frame')
        notifyReturnFrame = false
      }

      show(index)

      if (previous !== null && previous !== index) {
        acknowledge(slots[previous], 'interrupted')
        reset(slots[previous])
      }

      const next = deferredMedia
      deferredMedia = null

      if (next) {
        receive(next)
      }
    }

    const restoreLoop = (notifyFirstFrame = false): void => {
      if (retainedLoop === null && fallbackPoster && staticMedia) {
        if (active !== null) {
          captureTransition(slots[active])
          reset(slots[active])
        }

        active = null
        showPoster(fallbackPoster)
        fadeTransition()

        return
      }

      if (retainedLoop === null || !slots[retainedLoop].ready || slots[retainedLoop].terminal) {
        return
      }

      const index = retainedLoop
      const slot = slots[index]
      returningLoop = index
      returningFrom = active
      notifyReturnFrame = notifyFirstFrame
      slot.element.pause()
      slot.element.loop = slot.media?.transition !== 'fade'

      if (slot.element.currentTime === 0 && !slot.element.seeking) {
        finishReturn(index)
      } else {
        // 寻帧前登记首帧回调；seeked 触发时该帧可能已呈现，暂停状态不会再产生下一帧。
        waitForFrame(slot, () => finishReturn(index))
        slot.element.currentTime = 0
      }
    }

    const sharesBase = (loop: DesktopBackground | null, candidate: DesktopBackground): boolean =>
      loop?.kind === 'loop' &&
      loop.presentationEpoch === candidate.presentationEpoch &&
      loop.transition === 'natural' &&
      candidate.transition === 'natural' &&
      loop.continuity === 'base' &&
      candidate.continuity === 'base' &&
      loop.inputImageHash !== null &&
      loop.inputImageHash === candidate.inputImageHash

    const activate = (index: number): void => {
      const slot = slots[index]

      if (!slot.media || !slot.ready || slot.terminal) {
        return
      }

      if (slot.media.expiresAt && Date.parse(slot.media.expiresAt) <= Date.now()) {
        acknowledge(slot, 'failed', 'Playback request expired before playback started')
        reset(slot)

        if (active !== null) {
          slots[active].element.loop =
            slots[active].media?.kind === 'loop' && slots[active].media?.transition !== 'fade'
          start(slots[active])
        }

        return
      }

      if (slot.timeout) {
        clearTimeout(slot.timeout)
        slot.timeout = null
      }

      pending = null
      const previous = active

      if (
        slot.media.kind === 'once' &&
        previous !== null &&
        slots[previous].media?.kind === 'loop' &&
        slots[previous].media?.presentationEpoch === slot.media.presentationEpoch
      ) {
        retainedLoop = previous
      } else {
        retainedLoop = null
      }

      show(index)

      if (previous !== null && previous !== index && previous !== retainedLoop) {
        acknowledge(slots[previous], 'interrupted')
        reset(slots[previous])
      }
    }

    const onLoaded = (index: number): void => {
      const slot = slots[index]

      if (!slot.media || slot.ready || slot.terminal) {
        return
      }

      if (slot.media.expiresAt && Date.parse(slot.media.expiresAt) <= Date.now()) {
        acknowledge(slot, 'failed', 'Playback request expired before the first frame was ready')
        reset(slot)

        return
      }

      if (slot.timeout) {
        clearTimeout(slot.timeout)
        slot.timeout = null
      }

      slot.ready = true
      updateVideoLayout(slot)
      acknowledge(slot, 'first-frame')

      if (slot.media.kind === 'once' && active !== null && sharesBase(slots[active].media, slot.media)) {
        pending = index
        // 保底循环继续到自然结束，已解码的短动作从共同基础画面开始。
        slots[active].element.loop = false

        if (slot.media.expiresAt) {
          slot.timeout = setTimeout(
            () => {
              if (pending === index) {
                acknowledge(slot, 'failed', 'Playback request expired while waiting for the loop boundary')
                reset(slot)
              }
            },
            Math.max(0, Date.parse(slot.media.expiresAt) - Date.now())
          )
        }

        return
      }

      activate(index)
    }

    const listeners = slots.map((slot, index) => {
      const loaded = (): void => {
        if (slot.media && !slot.ready && !slot.terminal && slot.frameCallback === null) {
          waitForFrame(slot, () => onLoaded(index))
        }
      }

      const playing = (): void => {
        if (active === index && !slot.started && !slot.terminal) {
          slot.started = true
          acknowledge(slot, 'started')
        }
      }

      const resized = (): void => updateVideoLayout(slot)

      const ended = (): void => {
        if (active !== index || !slot.element.ended) {
          return
        }

        if (slot.media?.kind === 'loop' && pending !== null) {
          activate(pending)
        } else if (slot.media?.kind === 'loop' && slot.media.transition === 'fade') {
          captureTransition(slot)
          waitForFrame(slot, fadeTransition)
          slot.element.currentTime = 0
          start(slot)
        } else if (slot.media?.kind === 'once') {
          // 终态交给宿主重读最新基础舞台，不能直接恢复演出开始时保留的旧循环。
          acknowledge(slot, 'completed')
        }
      }

      const failed = (): void => {
        if (slot.media && !slot.terminal && slot.element.error) {
          acknowledge(slot, 'failed', slot.element.error?.message || 'Video could not be decoded')

          if (active !== index) {
            reset(slot)
          }
        }
      }

      slot.element.addEventListener('loadeddata', loaded)
      slot.element.addEventListener('playing', playing)
      slot.element.addEventListener('resize', resized)
      slot.element.addEventListener('ended', ended)
      slot.element.addEventListener('error', failed)

      return () => {
        slot.element.removeEventListener('loadeddata', loaded)
        slot.element.removeEventListener('playing', playing)
        slot.element.removeEventListener('resize', resized)
        slot.element.removeEventListener('ended', ended)
        slot.element.removeEventListener('error', failed)
      }
    })

    const clear = (): void => {
      imageGeneration += 1

      if (staticMedia) {
        acknowledgeImage(staticMedia, 'interrupted')
        staticMedia = null
      }

      resetActor()
      returningLoop = null
      returningFrom = null
      notifyReturnFrame = false

      if (fadeTimer) {
        clearTimeout(fadeTimer)
      }

      if (fadeFrame !== null) {
        cancelAnimationFrame(fadeFrame)
      }

      transition.style.opacity = '0'
      interruptDeferred()
      deferredMedia = null

      for (const slot of slots) {
        acknowledge(slot, 'interrupted')
        reset(slot)
      }

      active = null
      retainedLoop = null
      pending = null
      settled.clear()

      if (fallbackPoster) {
        URL.revokeObjectURL(fallbackPoster)
        fallbackPoster = null
      }

      setFill(null)
      still.removeAttribute('src')
      showPoster(null)
    }

    const receive = (request: DesktopBackground): void => {
      const media: DesktopBackground =
        request.playTarget === 'actor' ? { ...request, playId: null, kind: 'loop', expiresAt: null } : request

      if (media.accountEpoch < accountEpoch || (media.accountEpoch === accountEpoch && media.revision < revision)) {
        return
      }

      if (media.accountEpoch !== accountEpoch || media.clear) {
        clear()
        accountEpoch = media.accountEpoch
      }

      revision = media.revision
      actorLayout = media.layout
      applyLayerOrder()
      const nextImageGeneration = ++imageGeneration
      paused = media.paused || media.reduceMotion
      phaseMotionEnabled.current = !media.reduceMotion
      setPhaseFilter(ambientPhaseFilter(phaseMotionEnabled.current))
      document.documentElement.dataset.theme = media.theme
      document.documentElement.dataset.palette = media.theme.startsWith('night') ? 'night' : 'day'

      if (media.clear) {
        requestAnimationFrame(() => {
          if (!disposed && nextImageGeneration === imageGeneration) {
            acknowledgeImage(media, 'started')
          }
        })

        return
      }

      // 主体层与背景层独立接收：环境层换图不打断主体，主体缺失只清自身。
      receiveActor(request)

      for (const slot of slots) {
        if (slot.media?.playId === media.playId && (media.playId !== null || slot.media.revision === media.revision)) {
          slot.media = {
            ...slot.media,
            revision: media.revision,
            geometryMode: media.geometryMode,
            paused,
            reduceMotion: media.reduceMotion
          }
          slot.element.style.zIndex = layerZ(slot.media.actor ? 'scene' : 'cinematic')
          updateVideoLayout(slot)
        }
      }

      const existing = slots.findIndex(
        slot =>
          slot.media &&
          slot.media.playId === media.playId &&
          (media.playId !== null || slot.media.revision === media.revision)
      )

      if (existing !== -1 || (media.playId && settled.has(media.playId))) {
        if (existing !== -1 && active === null) {
          showPoster(slots[existing].posterUrl)
        }

        if (active !== null) {
          start(slots[active])
        }

        return
      }

      if (returningLoop !== null) {
        if (deferredMedia?.playId && deferredMedia.playId !== media.playId) {
          interruptDeferred()
        }

        deferredMedia = media

        return
      }

      const reusableLoop = slots.findIndex(
        slot =>
          slot.media?.kind === 'loop' &&
          media.kind === 'loop' &&
          slot.ready &&
          !slot.terminal &&
          slot.media.visualId === media.visualId &&
          slot.media.presentationEpoch === media.presentationEpoch &&
          slot.media.visualVersion === media.visualVersion &&
          slot.media.continuity === media.continuity &&
          slot.media.transition === media.transition &&
          slot.media.geometryMode === media.geometryMode &&
          slot.media.inputImageHash === media.inputImageHash
      )

      if (media.video && reusableLoop !== -1) {
        if (pending !== null) {
          acknowledge(slots[pending], 'interrupted')
          reset(slots[pending])
        }

        const slot = slots[reusableLoop]
        acknowledge(slot, 'interrupted')
        slot.media = media
        slot.started = false
        slot.terminal = false

        if (active === reusableLoop && media.playId === null) {
          retainedLoop = null
          start(slot)

          return
        }

        retainedLoop = reusableLoop
        restoreLoop(true)

        return
      }

      if (!media.video) {
        if (!media.poster) {
          if (media.actor) {
            for (const slot of slots) {
              reset(slot)
            }

            active = retainedLoop = pending = null
            still.style.opacity = '0'
            setFill(null)
            allowActor(true)
          }

          return
        }

        if (
          staticMedia?.playId === media.playId &&
          staticMedia?.visualId === media.visualId &&
          staticMedia?.visualVersion === media.visualVersion
        ) {
          return
        }

        let preparedPoster: string | null = null
        void prepareStaticPoster(media.poster, media.actor ? null : media.outfit)
          .then(nextPoster => {
            preparedPoster = nextPoster

            if (disposed || nextImageGeneration !== imageGeneration) {
              URL.revokeObjectURL(nextPoster)

              return
            }

            if (media.expiresAt && Date.parse(media.expiresAt) <= Date.now()) {
              acknowledgeImage(media, 'failed', 'Image request expired before display')
              URL.revokeObjectURL(nextPoster)

              return
            }

            if (active !== null) {
              captureTransition(slots[active])
            }

            if (staticMedia) {
              acknowledgeImage(staticMedia, 'interrupted')
            }

            for (const slot of slots) {
              acknowledge(slot, 'interrupted')
              reset(slot)
            }

            active = null
            retainedLoop = null
            const previousPoster = fallbackPoster
            fallbackPoster = nextPoster
            staticMedia = media
            allowActor(Boolean(media.actor))
            showPoster(nextPoster)
            fadeTransition()
            requestAnimationFrame(() => {
              if (!disposed && nextImageGeneration === imageGeneration) {
                acknowledgeImage(media, 'started')
              }

              if (previousPoster) {
                URL.revokeObjectURL(previousPoster)
              }
            })
          })
          .catch(error => {
            if (preparedPoster) {
              URL.revokeObjectURL(preparedPoster)
            }

            if (!disposed && nextImageGeneration === imageGeneration) {
              acknowledgeImage(media, 'failed', String(error))
            }
          })

        return
      }

      // 新套装或持续画面替换不能返回已失效的保留循环。
      if (retainedLoop !== null && media.kind === 'loop') {
        const retained = slots[retainedLoop]

        if (
          retained.media?.visualId !== media.visualId ||
          retained.media?.presentationEpoch !== media.presentationEpoch ||
          retained.media?.visualVersion !== media.visualVersion
        ) {
          acknowledge(retained, 'interrupted')
          reset(retained)
          retainedLoop = null
        }
      }

      // 新动作到达时先回到保留的持续状态，另一缓冲才可安全解码。
      if (active !== null && slots[active].media?.kind === 'once' && retainedLoop !== null) {
        acknowledge(slots[active], 'interrupted')
        restoreLoop()

        if (returningLoop !== null) {
          deferredMedia = media

          return
        }
      }

      const next = active === 0 ? 1 : 0
      const slot = slots[next]

      if (slot.media) {
        acknowledge(slot, 'interrupted')
      }

      reset(slot)
      slot.media = media
      slot.videoUrl = URL.createObjectURL(new Blob([new Uint8Array(media.video.bytes)], { type: media.video.mime }))
      slot.posterUrl = media.poster
        ? URL.createObjectURL(new Blob([new Uint8Array(media.poster.bytes)], { type: media.poster.mime }))
        : null
      slot.element.loop = media.kind === 'loop' && media.transition !== 'fade'
      slot.element.style.objectFit = 'contain'
      slot.element.src = slot.videoUrl

      if (slot.posterUrl) {
        slot.element.poster = slot.posterUrl

        if (active === null && !staticMedia) {
          showPoster(slot.posterUrl)
        }
      }

      slot.timeout = setTimeout(() => {
        slot.timeout = null

        if (!slot.ready) {
          acknowledge(slot, 'failed', 'Video first frame timed out')
          reset(slot)
        }
      }, 30000)
      slot.element.load()
      waitForFrame(slot, () => onLoaded(next))

      if (active !== null && paused) {
        slots[active].element.pause()
      }
    }

    const off = bridge.onMedia(receive)

    window.addEventListener('resize', updateAllMediaLayouts)
    still.addEventListener('load', updatePosterLayout)

    void bridge.ready().catch(error => console.warn('Desktop background readiness failed', error))

    return () => {
      off()
      clear()
      disposed = true
      actorLayer.dispose()
      listeners.forEach(remove => remove())
      window.removeEventListener('resize', updateAllMediaLayouts)
      still.removeEventListener('load', updatePosterLayout)
    }
  }, [])

  const videoStyle = {
    position: 'absolute',
    left: 0,
    top: 0,
    width: '100%',
    height: '100%',
    objectFit: 'contain',
    opacity: 0
  } as const

  // 时段氛围色调只作用于环境（背景/poster），主体层保持素材原色；每小时复查。
  const [phaseFilter, setPhaseFilter] = useState(() => ambientPhaseFilter(true))

  useEffect(() => {
    const timer = window.setInterval(() => setPhaseFilter(ambientPhaseFilter(phaseMotionEnabled.current)), 60 * 60_000)

    return () => window.clearInterval(timer)
  }, [])

  return (
    <div
      aria-hidden="true"
      style={{ position: 'fixed', inset: 0, overflow: 'hidden', backgroundColor: 'var(--ui-app-bg)' }}
    >
      <div
        ref={backdrop}
        style={{
          position: 'absolute',
          inset: -32,
          backgroundPosition: 'center',
          backgroundSize: 'cover',
          filter: phaseFilter === 'none' ? 'blur(24px) brightness(.6)' : `blur(24px) brightness(.6) ${phaseFilter}`
        }}
      />
      <img
        alt=""
        ref={poster}
        style={{ ...videoStyle, zIndex: 1, ...(phaseFilter !== 'none' ? { filter: phaseFilter } : {}) }}
      />
      <video muted playsInline preload="auto" ref={first} style={videoStyle} />
      <video muted playsInline preload="auto" ref={second} style={videoStyle} />
      <div ref={actorRoot} style={{ position: 'absolute', inset: 0, zIndex: 2, opacity: 0 }}>
        <img
          alt=""
          ref={actorPoster}
          style={{ ...videoStyle, height: '90%', width: '50%', left: '25%', top: '10%', objectPosition: 'bottom' }}
        />
        <video muted playsInline preload="auto" ref={actorFirst} style={videoStyle} />
        <video muted playsInline preload="auto" ref={actorSecond} style={videoStyle} />
      </div>
      <canvas ref={transitionFrame} style={{ ...videoStyle, zIndex: 3, pointerEvents: 'none' }} />
    </div>
  )
}

const root = document.getElementById('root')

if (!root) {
  throw new Error('desktop-background: missing root element')
}

createRoot(root).render(<DesktopWallpaper />)
