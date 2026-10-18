import '../../../styles.css'

import { useEffect, useRef } from 'react'
import { createRoot } from 'react-dom/client'

import { backgroundMedia } from '@/shared/platform/desktop'
import { desktopMediaLayout } from '@client-shared/desktop-media-geometry'
import type { DesktopBackground, DesktopBackgroundPlayback } from '@ipc/contracts'

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

  useEffect(() => {
    const bridge = backgroundMedia()
    const firstVideo = first.current
    const secondVideo = second.current
    const fill = backdrop.current
    const still = poster.current
    const transition = transitionFrame.current

    if (!bridge || !firstVideo || !secondVideo || !fill || !still || !transition) {
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
    let fadeTimer: ReturnType<typeof setTimeout> | null = null
    let fadeFrame: number | null = null
    const settled = new Set<string>()

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

    const updateMediaLayout = (element: HTMLImageElement | HTMLVideoElement, width: number, height: number): void => {
      const layout = desktopMediaLayout(width, height, window.innerWidth, window.innerHeight)

      // 完整显示时媒体元素仅占实际内容区域，周围由模糊底层填充。
      Object.assign(element.style, {
        left: `${layout.left}px`,
        top: `${layout.top}px`,
        width: `${layout.width}px`,
        height: `${layout.height}px`,
        objectFit: layout.objectFit
      })
    }

    const updatePosterLayout = (): void => updateMediaLayout(still, still.naturalWidth, still.naturalHeight)

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
      updateMediaLayout(slot.element, slot.element.videoWidth, slot.element.videoHeight)

    const updateAllMediaLayouts = (): void => {
      slots.forEach(updateVideoLayout)
      updatePosterLayout()
    }

    const start = (slot: VideoSlot): void => {
      if (paused || !slot.ready || slot.terminal) {
        slot.element.pause()

        return
      }

      if (!slot.started && slot.media?.expiresAt && Date.parse(slot.media.expiresAt) <= Date.now()) {
        acknowledge(slot, 'failed', 'Playback request expired before playback started')
        restoreLoop()

        return
      }

      const generation = slot.generation

      void slot.element.play().catch(error => {
        if (!paused && slot.generation === generation && slot.ready && !slot.terminal) {
          acknowledge(slot, 'failed', String(error))
          restoreLoop()
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
      slot.element.style.opacity = '1'
      slot.element.style.zIndex = '2'
      still.style.opacity = '0'
      showPoster(slot.posterUrl)

      for (let i = 0; i < slots.length; i += 1) {
        if (i !== index) {
          slots[i].element.style.opacity = '0'
          slots[i].element.style.zIndex = '1'
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
      loop.setId === candidate.setId &&
      loop.setEpoch === candidate.setEpoch &&
      loop.transition === 'base' &&
      candidate.transition === 'base' &&
      loop.continuity === 'base' &&
      candidate.continuity === 'base' &&
      loop.baseVersion !== null &&
      loop.baseVersion === candidate.baseVersion

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
        slots[previous].media?.setId === slot.media.setId &&
        slots[previous].media?.setEpoch === slot.media.setEpoch
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
        if (active !== index) {
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
          acknowledge(slot, 'completed')
          restoreLoop()
        }
      }

      const failed = (): void => {
        if (slot.media && !slot.terminal) {
          acknowledge(slot, 'failed', slot.element.error?.message || 'Video could not be decoded')

          if (active === index) {
            restoreLoop()
          } else {
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

    const receive = (media: DesktopBackground): void => {
      if (media.accountEpoch < accountEpoch || (media.accountEpoch === accountEpoch && media.revision < revision)) {
        return
      }

      if (media.accountEpoch !== accountEpoch || media.clear) {
        clear()
        accountEpoch = media.accountEpoch
      }

      revision = media.revision
      paused = media.paused || media.reduceMotion
      document.documentElement.dataset.theme = media.theme
      document.documentElement.dataset.palette = media.theme.startsWith('night') ? 'night' : 'day'

      for (const slot of slots) {
        if (slot.media?.playId === media.playId && (media.playId !== null || slot.media.revision === media.revision)) {
          slot.media = {
            ...slot.media,
            revision: media.revision,
            geometryMode: media.geometryMode,
            paused,
            reduceMotion: media.reduceMotion
          }
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
          slot.media.setId === media.setId &&
          slot.media.setEpoch === media.setEpoch &&
          slot.media.actionId === media.actionId &&
          slot.media.actionVersion === media.actionVersion &&
          slot.media.continuity === media.continuity &&
          slot.media.transition === media.transition &&
          slot.media.geometryMode === media.geometryMode &&
          slot.media.baseVersion === media.baseVersion
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
        if (active === null) {
          if (fallbackPoster) {
            if (fillPosterUrl === fallbackPoster) {
              setFill(null)
            }

            URL.revokeObjectURL(fallbackPoster)
          }

          fallbackPoster = media.poster
            ? URL.createObjectURL(new Blob([new Uint8Array(media.poster.bytes)], { type: media.poster.mime }))
            : null
          showPoster(fallbackPoster)
        }

        if (active !== null) {
          start(slots[active])
        }

        return
      }

      // 新套装或持续画面替换不能返回已失效的保留循环。
      if (retainedLoop !== null && media.kind === 'loop') {
        const retained = slots[retainedLoop]

        if (
          retained.media?.setId !== media.setId ||
          retained.media?.setEpoch !== media.setEpoch ||
          retained.media?.actionId !== media.actionId ||
          retained.media?.actionVersion !== media.actionVersion
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

        if (active === null) {
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
          filter: 'blur(24px) brightness(.6)'
        }}
      />
      <img alt="" ref={poster} style={{ ...videoStyle, zIndex: 1 }} />
      <video muted playsInline preload="auto" ref={first} style={videoStyle} />
      <video muted playsInline preload="auto" ref={second} style={videoStyle} />
      <canvas ref={transitionFrame} style={{ ...videoStyle, zIndex: 3, pointerEvents: 'none' }} />
    </div>
  )
}

const root = document.getElementById('root')

if (!root) {
  throw new Error('desktop-background: missing root element')
}

createRoot(root).render(<DesktopWallpaper />)
