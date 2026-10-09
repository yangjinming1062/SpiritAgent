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
  generation: number
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
      generation: 0
    }))

    let active: number | null = null
    let retainedLoop: number | null = null
    let accountEpoch = -1
    let revision = -1
    let paused = false
    let fallbackPoster: string | null = null
    let disposed = false
    const settled = new Set<string>()
    const fadeTimers = new Set<ReturnType<typeof setTimeout>>()

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

    const reset = (slot: VideoSlot): void => {
      slot.generation += 1

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
        URL.revokeObjectURL(slot.posterUrl)
      }

      slot.media = null
      slot.videoUrl = null
      slot.posterUrl = null
      slot.ready = false
      slot.started = false
      slot.terminal = false
    }

    const showPoster = (url: string | null): void => {
      fill.style.backgroundImage = url ? `url("${url}")` : ''

      if (url && active === null) {
        still.src = url
        still.style.opacity = '1'
      } else {
        still.style.opacity = '0'
      }
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

      void slot.element.play().catch(error => {
        if (!paused && slot.ready && !slot.terminal) {
          acknowledge(slot, 'failed', String(error))
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

    const restoreLoop = (): void => {
      if (retainedLoop !== null && slots[retainedLoop].ready && !slots[retainedLoop].terminal) {
        show(retainedLoop)
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
      acknowledge(slot, 'first-frame')
      const previous = active

      if (slot.media.kind === 'once' && previous !== null && slots[previous].media?.kind === 'loop') {
        retainedLoop = previous
      } else if (slot.media.kind === 'loop') {
        retainedLoop = null
      }

      show(index)

      if (previous !== null && previous !== index && previous !== retainedLoop) {
        const old = slots[previous]
        acknowledge(old, 'interrupted')
        const oldGeneration = old.generation

        const timer = setTimeout(
          () => {
            fadeTimers.delete(timer)

            if (active !== previous && slots[previous].generation === oldGeneration && retainedLoop !== previous) {
              reset(slots[previous])
            }
          },
          slot.media.reduceMotion ? 0 : 350
        )

        fadeTimers.add(timer)
      }
    }

    const listeners = slots.map((slot, index) => {
      const loaded = (): void => onLoaded(index)

      const playing = (): void => {
        if (active === index && !slot.started && !slot.terminal) {
          slot.started = true
          acknowledge(slot, 'started')
        }
      }

      const ended = (): void => {
        if (active === index && slot.media?.kind === 'once') {
          acknowledge(slot, 'completed')
          restoreLoop()
        }
      }

      const failed = (): void => {
        if (slot.media && !slot.terminal) {
          acknowledge(slot, 'failed', slot.element.error?.message || 'Video could not be decoded')

          if (active === index) {
            restoreLoop()
          }
        }
      }

      slot.element.addEventListener('loadeddata', loaded)
      slot.element.addEventListener('playing', playing)
      slot.element.addEventListener('ended', ended)
      slot.element.addEventListener('error', failed)

      return () => {
        slot.element.removeEventListener('loadeddata', loaded)
        slot.element.removeEventListener('playing', playing)
        slot.element.removeEventListener('ended', ended)
        slot.element.removeEventListener('error', failed)
      }
    })

    const clear = (): void => {
      for (const slot of slots) {
        acknowledge(slot, 'interrupted')
        reset(slot)
      }

      active = null
      retainedLoop = null
      settled.clear()

      if (fallbackPoster) {
        URL.revokeObjectURL(fallbackPoster)
        fallbackPoster = null
      }

      still.removeAttribute('src')
      showPoster(null)
    }

    const off = bridge.onMedia(media => {
      if (media.accountEpoch === accountEpoch && media.revision < revision) {
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
        slot.element.style.transition = media.reduceMotion ? 'none' : 'opacity 300ms ease'

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

      if (!media.video) {
        if (active === null) {
          if (fallbackPoster) {
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

      if (active !== null && paused) {
        slots[active].element.pause()
      }
    })

    void bridge.ready().catch(error => console.warn('Desktop background readiness failed', error))

    return () => {
      off()
      clear()
      disposed = true
      listeners.forEach(remove => remove())
      fadeTimers.forEach(timer => clearTimeout(timer))
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
      <img alt="" ref={poster} style={{ ...videoStyle, zIndex: 1 }} />
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
