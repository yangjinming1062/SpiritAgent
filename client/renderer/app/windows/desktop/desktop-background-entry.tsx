import '../../../styles.css'

import { useEffect, useRef } from 'react'
import { createRoot } from 'react-dom/client'

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

const BASELINE_VIDEO_ASPECT = 16 / 9
const BASELINE_ASPECT_TOLERANCE = 0.07
const MAX_COVER_CROP = 0.28
const FALLBACK_BACKGROUND = 'radial-gradient(circle at center, var(--ui-fill-hover), var(--ui-app-bg) 72%)'

function selectVideoObjectFit(
  videoWidth: number,
  videoHeight: number,
  viewportWidth = window.innerWidth,
  viewportHeight = window.innerHeight
): 'contain' | 'cover' {
  if (
    !Number.isFinite(videoWidth) ||
    !Number.isFinite(videoHeight) ||
    videoWidth <= 0 ||
    videoHeight <= 0 ||
    !Number.isFinite(viewportWidth) ||
    !Number.isFinite(viewportHeight) ||
    viewportWidth <= 0 ||
    viewportHeight <= 0
  ) {
    return 'contain'
  }

  const videoAspect = videoWidth / videoHeight
  const viewportAspect = viewportWidth / viewportHeight

  // 非 16:9 资产保留完整画面，避免把用户上传的窄幅或超宽视频强行裁切。
  if (Math.abs(videoAspect - BASELINE_VIDEO_ASPECT) > BASELINE_ASPECT_TOLERANCE) {
    return 'contain'
  }

  // 桌面模式当前只需处理超宽屏；较窄视口使用 contain，避免裁掉左右主体。
  if (viewportAspect <= videoAspect) {
    return 'contain'
  }

  const cropFraction = 1 - videoAspect / viewportAspect

  // 常见 21:9（含 3440×1440）相对 16:9 的上下裁切约 24%～26%；32:9 会接近 50%，改用模糊铺底。
  return cropFraction <= MAX_COVER_CROP ? 'cover' : 'contain'
}

// 壁纸表面只消费主进程取得的账户媒体字节，没有鉴权、API 或会话能力。
function DesktopWallpaper() {
  const first = useRef<HTMLVideoElement>(null)
  const second = useRef<HTMLVideoElement>(null)
  const backdrop = useRef<HTMLDivElement>(null)
  const poster = useRef<HTMLImageElement>(null)

  useEffect(() => {
    const bridge = window.desktopBackground
    const firstVideo = first.current
    const secondVideo = second.current
    const fill = backdrop.current
    const still = poster.current

    if (!bridge || !firstVideo || !secondVideo || !fill || !still) {
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
          slots[active].element.loop = true
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

    const showPoster = (url: string | null): void => {
      if (url) {
        setFill(url)
      } else if (!fillPosterUrl) {
        setFill(null)
      }

      if (url && active === null) {
        still.src = url
        still.style.opacity = '1'
      } else {
        still.style.opacity = '0'
      }
    }

    const updateVideoFit = (slot: VideoSlot): void => {
      slot.element.style.objectFit = selectVideoObjectFit(
        slot.element.videoWidth,
        slot.element.videoHeight,
        slot.element.clientWidth || window.innerWidth,
        slot.element.clientHeight || window.innerHeight
      )
      slot.element.style.objectPosition = 'center center'
    }

    const updateAllVideoFits = (): void => {
      slots.forEach(updateVideoFit)
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

    const show = (index: number): void => {
      const slot = slots[index]
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

      const slot = slots[retainedLoop]
      returningLoop = retainedLoop
      returningFrom = active
      notifyReturnFrame = notifyFirstFrame
      slot.element.pause()
      slot.element.loop = true

      if (slot.element.currentTime === 0 && !slot.element.seeking) {
        finishReturn(retainedLoop)
      } else {
        // 保留短动作的末帧，直到持续画面的起点完成寻帧与呈现。
        slot.element.currentTime = 0
      }
    }

    const sharesBase = (loop: DesktopBackground | null, candidate: DesktopBackground): boolean =>
      loop?.kind === 'loop' &&
      loop.setId === candidate.setId &&
      loop.setEpoch === candidate.setEpoch &&
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
          slots[active].element.loop = slots[active].media?.kind === 'loop'
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
      updateVideoFit(slot)
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

      const seeked = (): void => {
        if (returningLoop === index) {
          waitForFrame(slot, () => finishReturn(index))
        }
      }

      const playing = (): void => {
        if (active === index && !slot.started && !slot.terminal) {
          slot.started = true
          acknowledge(slot, 'started')
        }
      }

      const ended = (): void => {
        if (active !== index) {
          return
        }

        if (slot.media?.kind === 'loop' && pending !== null) {
          activate(pending)
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
      slot.element.addEventListener('seeked', seeked)
      slot.element.addEventListener('playing', playing)
      slot.element.addEventListener('ended', ended)
      slot.element.addEventListener('error', failed)

      return () => {
        slot.element.removeEventListener('loadeddata', loaded)
        slot.element.removeEventListener('seeked', seeked)
        slot.element.removeEventListener('playing', playing)
        slot.element.removeEventListener('ended', ended)
        slot.element.removeEventListener('error', failed)
      }
    })

    const clear = (): void => {
      returningLoop = null
      returningFrom = null
      notifyReturnFrame = false
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
          slot.media = { ...slot.media, revision: media.revision, paused, reduceMotion: media.reduceMotion }
        }
      }

      const existing = slots.findIndex(
        slot =>
          slot.media &&
          slot.media.playId === media.playId &&
          (media.playId !== null || slot.media.revision === media.revision)
      )

      if (existing !== -1 || (media.playId && settled.has(media.playId))) {
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
      slot.element.loop = media.kind === 'loop'
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

    window.addEventListener('resize', updateAllVideoFits)

    void bridge.ready().catch(error => console.warn('Desktop background readiness failed', error))

    return () => {
      off()
      clear()
      disposed = true
      listeners.forEach(remove => remove())
      window.removeEventListener('resize', updateAllVideoFits)
    }
  }, [])

  const videoStyle = {
    position: 'absolute',
    inset: 0,
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
      <img alt="" ref={poster} style={{ ...videoStyle, objectFit: 'cover', zIndex: 1 }} />
      <video muted playsInline preload="auto" ref={first} style={videoStyle} />
      <video muted playsInline preload="auto" ref={second} style={videoStyle} />
    </div>
  )
}

const root = document.getElementById('root')

if (!root) {
  throw new Error('desktop-background: missing root element')
}

createRoot(root).render(<DesktopWallpaper />)
