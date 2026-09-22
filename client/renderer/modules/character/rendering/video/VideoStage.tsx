/** 视频渲染层：挂载透明 WebM 片段，按动作切换，双 video 交替避免黑帧。
 * 片段播放为原地循环；移动与拖拽由容器位移表达（spatial 是位置真源）。
 * 命中：按当前片段的 alpha 命中遮罩查表（容器平移与缩放已由舞台坐标归一化）。 */

import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import React, { useEffect, useRef, useState } from 'react'

import {
  $spatialLocomotion,
  $spatialPos,
  $spriteContentRect,
  pickAvailableClip,
  resolveVideoAction,
  type VideoActionKey
} from '@/modules/character'
import { log } from '@/shared/lib/log'

import type { VideoClipSpec } from './types'
import {
  $videoPack,
  $videoPackStatus,
  type ActiveVideoPack,
  requestMissingVideoAction,
  resolveVideoClipUrl
} from './video-pack-store'

// 命中探测（舞台像素坐标）：返回 true 命中身体 / false 透明 / null 无数据。
export const $videoHitTest = atom<((nx: number, ny: number) => boolean | null) | null>(null)

function useCurrentAction(): VideoActionKey {
  const [action, setAction] = useState<VideoActionKey>('idle')
  const lastXRef = useRef<number | null>(null)

  useEffect(() => {
    let raf = 0

    const tick = (): void => {
      const pos = $spatialPos.get()
      const last = lastXRef.current
      lastXRef.current = pos.x
      const deltaXSign = last === null || pos.x === last ? 0 : pos.x > last ? 1 : -1
      setAction(resolveVideoAction({ locomotion: $spatialLocomotion.get(), deltaXSign }))
      raf = window.setTimeout(tick, 120)
    }

    tick()

    return () => window.clearTimeout(raf)
  }, [])

  return action
}

function clipFor(pack: ActiveVideoPack, action: VideoActionKey): VideoClipSpec {
  const available = new Set(pack.manifest.clips.map(c => c.action))
  const wanted = pickAvailableClip(action, available, pack.manifest.default_action)

  return (
    pack.manifest.clips.find(c => c.action === wanted) ??
    pack.manifest.clips.find(c => c.action === pack.manifest.default_action) ??
    pack.manifest.clips[0]
  )
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

export function VideoStage(): React.JSX.Element {
  const pack = useStore($videoPack)
  const action = useCurrentAction()
  const videos = useRef<[HTMLVideoElement | null, HTMLVideoElement | null]>([null, null])
  const front = useRef<number | null>(null)
  const [visible, setVisible] = useState<number | null>(null)
  const currentClip = useRef<VideoClipSpec | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const canvas = pack?.manifest.canvas

  useEffect(() => {
    if (!pack) {
      return
    }

    const clip = clipFor(pack, action)

    // 缺素材时自动补齐；仅在明确动作上触发，待机回退不发请求。
    if (clip.action !== action && action !== 'idle') {
      void requestMissingVideoAction(pack, action)
    }

    if (currentClip.current?.path === clip.path) {
      return
    }

    const controller = new AbortController()
    let pauseTimer = 0

    const slot = front.current === 0 ? 1 : 0
    const elements = videos.current
    const el = elements[slot]

    void (async () => {
      if (!el) {
        return
      }

      try {
        const url = await resolveVideoClipUrl(pack, clip.action)

        if (controller.signal.aborted) {
          return
        }

        if (!url) {
          throw new Error('Video asset unavailable')
        }

        await loadVideo(el, url, controller.signal)

        if (controller.signal.aborted) {
          return
        }

        const previous = front.current
        front.current = slot
        currentClip.current = clip
        setVisible(slot)
        pauseTimer = window.setTimeout(() => {
          if (previous !== null && front.current !== previous) {
            elements[previous]?.pause()
          }
        }, 140)
      } catch (error) {
        if (controller.signal.aborted) {
          return
        }

        log.warn('video-stage', 'Could not play action', error)

        if (front.current === null) {
          $videoPackStatus.set('unavailable')
        }
      }
    })()

    return () => {
      controller.abort()
      window.clearTimeout(pauseTimer)

      for (let index = 0; index < elements.length; index += 1) {
        if (index !== front.current) {
          elements[index]?.pause()
        }
      }
    }
  }, [pack, action])

  useEffect(() => {
    if (!canvas) {
      return
    }

    $spriteContentRect.set({ left: 0, top: 0, right: canvas.width, bottom: canvas.height })

    return () => $spriteContentRect.set(null)
  }, [canvas])

  useEffect(() => {
    $videoHitTest.set((px, py) => {
      const clip = currentClip.current
      const el = front.current === null ? null : videos.current[front.current]
      const grid = clip?.hitmask_grid
      const rect = rootRef.current?.getBoundingClientRect()

      if (!clip || !grid || !el || !rect || !el.videoWidth || !el.videoHeight || !clip.hitmask.length) {
        return null
      }

      const scale = Math.min(rect.width / el.videoWidth, rect.height / el.videoHeight)
      const width = el.videoWidth * scale
      const height = el.videoHeight * scale
      const nx = (px - rect.left - (rect.width - width) / 2) / width
      const ny = (py - rect.top - (rect.height - height) / 2) / height

      if (nx < 0 || nx >= 1 || ny < 0 || ny >= 1) {
        return false
      }

      const [gw, gh] = grid
      const col = Math.floor(nx * gw)
      const row = Math.floor(ny * gh)
      const sample = clip.hitmask[Math.min(clip.hitmask.length - 1, Math.floor(el.currentTime * clip.hitmask_fps))]

      return ((sample?.[row] ?? 0) & (1 << col)) !== 0
    })
    const elements = videos.current

    return () => {
      $videoHitTest.set(null)

      for (const el of elements) {
        if (!el) {
          continue
        }

        el.pause()
        el.removeAttribute('src')
        el.load()
      }
    }
  }, [])

  return (
    <div className="relative h-full w-full" ref={rootRef}>
      {visible === null && pack?.coverUrl ? (
        <img alt="" className="absolute inset-0 h-full w-full object-contain" src={pack.coverUrl} />
      ) : null}
      {[0, 1].map(slot => (
        <video
          className="absolute inset-0 h-full w-full object-contain"
          key={slot}
          loop
          muted
          playsInline
          preload="auto"
          ref={el => {
            videos.current[slot] = el
          }}
          style={{ opacity: visible === slot ? 1 : 0, transition: 'opacity 120ms linear' }}
        />
      ))}
    </div>
  )
}
