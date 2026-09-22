/** 视频渲染层：挂载透明 WebM 片段，按统一调度器结果切换，双 video 交替避免黑帧。
 * 基础状态（idle/walk/drag）仍是表现优先级真源；动态动作为数据化表达请求
 * （play_id + appearance_epoch + TTL），由 actions 模块下发。
 * once 片段监听 ended，结束后回基础状态；循环素材默认播一次，repeat_count 有界。
 * 移动与拖拽由容器位移表达（spatial 是位置真源）。
 * 命中：按当前片段的 alpha 命中遮罩查表（容器平移与缩放已由舞台坐标归一化）。 */

import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import React, { useEffect, useRef, useState } from 'react'

import {
  $actionCatalog,
  $actionCatalogStatus,
  $activePlayInstance,
  $spatialLocomotion,
  $spatialPos,
  $spriteContentRect,
  type ActionHitmask,
  type ActionPlayInstance,
  finishPlayInstance,
  reportReceipt,
  resolveActionClipUrl,
  resolveHitmask,
  resolveVideoAction,
  shouldStartInstance,
  type VideoActionKey
} from '@/modules/character'
import { log } from '@/shared/lib/log'

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

/** 统一调度裁决：安全控制与拖拽/移动优先于表达；表达请求只在基础状态为 idle 时生效。
 * 抢占（拖拽/移动/新请求）时结清被替换实例：上报 interrupted 并结束其生命周期。 */
type Presentation =
  | { kind: 'expression'; instance: ActionPlayInstance; mountKey: string }
  | { kind: 'base'; action: VideoActionKey; mountKey: string }

function settleReplacedInstance(previous: ActionPlayInstance | null): void {
  if (previous === null) {
    return
  }

  // TTL 已过的被替换实例按过期收尾，不再上报中断（服务端同样按 TTL 过期处理）。
  if (previous.expiresAtMs !== null && Date.now() > previous.expiresAtMs) {
    finishPlayInstance(previous.generation)

    return
  }

  void reportReceipt({ play_id: previous.playId }, 'interrupted', 'preempted')
  finishPlayInstance(previous.generation)
}

function resolvePresentation(baseAction: VideoActionKey, instance: ActionPlayInstance | null): Presentation {
  if (instance !== null && baseAction === 'idle') {
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

interface MountedClip {
  key: string
  generation: number
  playId: string | null
}

export function VideoStage(): React.JSX.Element {
  const catalog = useStore($actionCatalog)
  const playInstance = useStore($activePlayInstance)
  const baseAction = useCurrentAction()
  const videos = useRef<[HTMLVideoElement | null, HTMLVideoElement | null]>([null, null])
  const front = useRef<number | null>(null)
  const [visible, setVisible] = useState<number | null>(null)
  const mounted = useRef<MountedClip | null>(null)
  const hitmaskRef = useRef<ActionHitmask | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const canvas = catalog?.manifest.canvas

  const presentation = resolvePresentation(baseAction, playInstance)

  // 目标 clip：表达实例按 action_id；基础动作查系统槽位；缺素材回退 idle 槽位（不自动付费补齐）。
  const targetClip =
    presentation.kind === 'expression'
      ? (catalog?.clipsById.get(presentation.instance.actionId) ?? null)
      : (catalog?.clipsBySlot.get(presentation.action) ?? null)

  const idleClip = catalog?.clipsBySlot.get('idle') ?? null
  const clip = targetClip ?? (presentation.kind === 'base' && presentation.action === 'idle' ? targetClip : idleClip)

  // 切换键含包与素材版本：A→B 外观即使同为 idle 也强制重载；同动作素材更新（asset_revision 推进）同样重载。
  const clipSwitchKey = clip ? `${clip.video_ref}@${clip.asset_revision}` : 'none'
  const mountKey = `${presentation.mountKey}|${catalog?.packId ?? 0}|${clipSwitchKey}`

  // 抢占结清：presentation 变化时，被替换的在途表达实例上报 interrupted 并结束。
  const prevInstanceRef = useRef<ActionPlayInstance | null>(null)
  useEffect(() => {
    const currentInstance = presentation.kind === 'expression' ? presentation.instance : null

    if (currentInstance !== null && currentInstance !== prevInstanceRef.current) {
      // 新表达请求替换旧实例（无论旧实例是否还在播放）。
      settleReplacedInstance(prevInstanceRef.current)
    } else if (currentInstance === null && prevInstanceRef.current !== null) {
      // 表达被基础动作（拖拽/移动/安全控制）抢占。
      settleReplacedInstance(prevInstanceRef.current)
    }

    prevInstanceRef.current = currentInstance
  }, [presentation])

  // 挂载/切换片段：相同 mountKey 不重复切换；新 play 实例（新 play_id）从头播放。
  useEffect(() => {
    if (!catalog || !clip) {
      return
    }

    if (mounted.current?.key === mountKey) {
      return
    }

    // 表达实例：loop 仅当素材可循环且请求了多次；基础动作持续循环。
    const loop = presentation.kind === 'base' || (clip.loopable && presentation.instance.repeatCount > 1)
    const instance = presentation.kind === 'expression' ? presentation.instance : null
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
        const url = await resolveActionClipUrl(catalog, clip)

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

        // 表达实例从头播放；真实可见后才上报 started（备用播放器预热不计）。
        if (instance !== null) {
          el.currentTime = 0
          el.loop = loop

          if (!shouldStartInstance(instance)) {
            return
          }

          void reportReceipt({ play_id: instance.playId }, 'started')
        } else {
          el.loop = true
        }

        const previous = front.current
        front.current = slot
        mounted.current = { key: mountKey, generation: instance?.generation ?? 0, playId: instance?.playId ?? null }
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

        if (instance !== null) {
          void reportReceipt({ play_id: instance.playId }, 'rejected', 'load failed')
          finishPlayInstance(instance.generation)
        } else if (!front.current) {
          // 首个基础片段加载失败：目录不可用，外层回落蛋形兜底（不悬空空白）。
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
        }
      }
    }
    // mountKey 已含 play_id / 包 / 素材版本 / 基础动作；clip 随 mountKey 唯一确定。
  }, [catalog, clip, mountKey, presentation])

  // 命中遮罩按 clip 加载：不随渲染重跑取消；切换动作时更新。
  useEffect(() => {
    if (!clip) {
      hitmaskRef.current = null

      return
    }

    let cancelled = false

    void resolveHitmask(clip).then(hm => {
      if (!cancelled) {
        hitmaskRef.current = hm
      }
    })

    return () => {
      cancelled = true
    }
  }, [clip])

  // 循环计数：repeat_count > 1 的 loop 表达在播满次数后 completed 并回基础状态
  //（el.loop=true 不触发 ended，手动计数）。
  useEffect(() => {
    const instance = presentation.kind === 'expression' ? presentation.instance : null

    if (instance === null || instance.repeatCount <= 1 || !instance.clip.loopable) {
      return
    }

    let played = 0
    let fired = false
    const elements = videos.current

    const handleLoopEnd = (): void => {
      const current = mounted.current

      if (!current || current.playId !== instance.playId || fired) {
        return
      }

      played += 1

      if (played >= instance.repeatCount) {
        fired = true
        void reportReceipt({ play_id: instance.playId }, 'completed', '', played * instance.clip.duration_ms)
        finishPlayInstance(instance.generation)
      }
    }

    // 时间更新近似周期边界：currentTime 回绕（新一轮开始）计一次。
    let lastTime = 0

    const handleTimeUpdate = (): void => {
      const el = front.current === null ? null : elements[front.current]

      if (!el) {
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

  // once 片段结束：上报 completed 并回到基础状态；旧回调凭 playId/generation 不影响新实例。
  useEffect(() => {
    const elements = videos.current
    const instance = presentation.kind === 'expression' ? presentation.instance : null
    const playId = instance?.playId ?? ''

    const handleEnded = (): void => {
      const current = mounted.current

      if (!current || !instance) {
        return
      }

      if (current.playId !== playId) {
        return
      }

      void reportReceipt({ play_id: playId }, 'completed', '', Math.round(instance.clip.duration_ms))
      finishPlayInstance(instance.generation)
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
    if (!canvas) {
      return
    }

    $spriteContentRect.set({ left: 0, top: 0, right: canvas.width, bottom: canvas.height })

    return () => $spriteContentRect.set(null)
  }, [canvas])

  useEffect(() => {
    $videoHitTest.set((px, py) => {
      const el = front.current === null ? null : videos.current[front.current]
      const hitmask = hitmaskRef.current
      const rect = rootRef.current?.getBoundingClientRect()

      if (!hitmask || !el || !rect || !el.videoWidth || !el.videoHeight || !hitmask.frames.length) {
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

      const [gw, gh] = hitmask.grid
      const col = Math.floor(nx * gw)
      const row = Math.floor(ny * gh)
      const sample = hitmask.frames[Math.min(hitmask.frames.length - 1, Math.floor(el.currentTime * hitmask.fps))]

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
      {[0, 1].map(slot => (
        <video
          className="absolute inset-0 h-full w-full object-contain"
          key={slot}
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
