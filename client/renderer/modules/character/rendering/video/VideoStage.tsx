/** 双 video 保留旧画面直到新帧就绪；位置与播放实例分别由 spatial、actions 管理。 */

import { useStore } from '@nanostores/react'
import { clamp } from '@runtime'
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { flushSync } from 'react-dom'

import {
  $actionCatalog,
  $actionCatalogStatus,
  $activePlayInstance,
  $peekPreparation,
  $screenLocked,
  $spatialLocomotion,
  $spatialPeek,
  $spatialPos,
  $spatialScale,
  $spriteCanvasRect,
  $spriteContentRect,
  $viewport,
  type ActionClipEntry,
  type ActionHitmask,
  type ActionPlayInstance,
  cancelPeekPreparation,
  commitPeekPreparation,
  finishPlayInstance,
  getBaseSpriteHeight,
  getBaseSpriteWidth,
  leavePeekForExpression,
  peekMaskRects,
  reportReceipt,
  resolveActionClipUrl,
  resolveHitmask,
  resolveVideoAction,
  restorePeekAfterExpression,
  shouldStartInstance,
  type VideoActionKey
} from '@/modules/character'
import { probeInteractiveRegions } from '@/shared/lib/interactive-regions'
import { log } from '@/shared/lib/log'
import { $chatVisible } from '@/shared/store/chat-visibility'

import { $videoHitTest } from './video-hit-test'

function useCurrentAction(): VideoActionKey {
  const [deltaXSign, setDeltaXSign] = useState(0)
  const locomotion = useStore($spatialLocomotion)
  const activePeek = useStore($spatialPeek)
  const preparation = useStore($peekPreparation)

  useEffect(() => {
    let timer = 0
    let lastX = $spatialPos.get().x

    const tick = (): void => {
      const x = $spatialPos.get().x
      setDeltaXSign(Math.sign(x - lastX))
      lastX = x
      timer = window.setTimeout(tick, 120)
    }

    tick()

    return () => window.clearTimeout(timer)
  }, [])

  return resolveVideoAction({ locomotion, deltaXSign, peekAction: activePeek?.action ?? preparation?.action ?? null })
}

type Presentation =
  | { kind: 'expression'; instance: ActionPlayInstance; mountKey: string }
  | { kind: 'base'; action: VideoActionKey; mountKey: string }

function settleReplacedInstance(previous: ActionPlayInstance | null): void {
  if (previous === null) {
    return
  }

  // 过期实例按 TTL 收尾，不另报抢占。
  if (previous.expiresAtMs !== null && Date.now() > previous.expiresAtMs) {
    finishPlayInstance(previous.generation)

    return
  }

  void reportReceipt({ play_id: previous.playId }, 'interrupted', 'preempted')
  finishPlayInstance(previous.generation)
}

function shouldStartVisibleInstance(instance: ActionPlayInstance): boolean {
  if (!shouldStartInstance(instance)) {
    return false
  }

  if ($screenLocked.get() || $chatVisible.get()) {
    settleReplacedInstance(instance)

    return false
  }

  return true
}

function resolvePresentation(
  baseAction: VideoActionKey,
  instance: ActionPlayInstance | null,
  deferExpression = false
): Presentation {
  if (instance !== null && baseAction === 'idle' && !deferExpression) {
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

function prepareFirstFrame(el: HTMLVideoElement, signal: AbortSignal): Promise<boolean> {
  return new Promise(resolve => {
    let settled = false
    let callbackId: number | null = null
    const timer = window.setTimeout(() => finish(false), 3000)

    const finish = (ready: boolean): void => {
      if (settled) {
        return
      }

      settled = true
      window.clearTimeout(timer)
      signal.removeEventListener('abort', aborted)

      if (callbackId !== null) {
        el.cancelVideoFrameCallback(callbackId)
      }

      resolve(ready)
    }

    const aborted = (): void => finish(false)
    signal.addEventListener('abort', aborted, { once: true })

    if (signal.aborted) {
      aborted()

      return
    }

    try {
      el.pause()
      el.currentTime = 0
      void el.play().then(
        () => {
          if (!settled) {
            callbackId = el.requestVideoFrameCallback(() => finish(true))
          }
        },
        () => finish(false)
      )
    } catch {
      finish(false)
    }
  })
}

interface MountedClip {
  key: string
  playId: string | null
}

export function VideoStage(): React.JSX.Element {
  const catalog = useStore($actionCatalog)
  const playInstance = useStore($activePlayInstance)
  const viewport = useStore($viewport)
  const baseAction = useCurrentAction()
  const spatialPeek = useStore($spatialPeek)
  const peekPreparation = useStore($peekPreparation)
  const videos = useRef<[HTMLVideoElement | null, HTMLVideoElement | null]>([null, null])
  const front = useRef<number | null>(null)
  const [visible, setVisible] = useState<number | null>(null)
  const [visibleClip, setVisibleClip] = useState<ActionClipEntry | null>(null)
  const mounted = useRef<MountedClip | null>(null)
  const peekExitGeneration = useRef<number | null>(null)
  const hitmaskRef = useRef<ActionHitmask | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const canvas = catalog?.manifest.canvas

  useEffect(() => {
    const cancelHiddenPlayback = (): void => {
      if ($screenLocked.get() || $chatVisible.get()) {
        settleReplacedInstance($activePlayInstance.get())
      }
    }

    const unlistenLock = $screenLocked.subscribe(cancelHiddenPlayback)
    const unlistenVisibility = $chatVisible.subscribe(cancelHiddenPlayback)

    return () => {
      unlistenLock()
      unlistenVisibility()
      settleReplacedInstance($activePlayInstance.get())
    }
  }, [])

  const canvasRect = useMemo(() => {
    if (!canvas) {
      return null
    }

    const stageH = Math.round(clamp(viewport.height / 3, 260, 960))
    const stageW = Math.round(stageH * 0.85)
    const contain = Math.min(stageW / canvas.width, stageH / canvas.height)
    const drawW = (canvas.width * contain) / stageW
    const drawH = (canvas.height * contain) / stageH

    return {
      left: (1 - drawW) / 2,
      top: (1 - drawH) / 2,
      right: (1 + drawW) / 2,
      bottom: (1 + drawH) / 2
    }
  }, [canvas, viewport.height])

  const deferExpressionForPeek = playInstance !== null && (spatialPeek !== null || peekPreparation !== null)

  const presentation = useMemo(
    () => resolvePresentation(baseAction, playInstance, deferExpressionForPeek),
    [baseAction, playInstance, deferExpressionForPeek]
  )

  useEffect(() => {
    if (playInstance && (spatialPeek || peekPreparation)) {
      if (peekExitGeneration.current !== playInstance.generation) {
        peekExitGeneration.current = playInstance.generation
        leavePeekForExpression()
      }

      return
    }

    if (!playInstance && !spatialPeek && peekExitGeneration.current !== null) {
      peekExitGeneration.current = null
      void restorePeekAfterExpression()
    }
  }, [playInstance, spatialPeek, peekPreparation])

  const targetClip =
    presentation.kind === 'expression'
      ? (catalog?.clipsById.get(presentation.instance.actionId) ?? null)
      : (catalog?.clipsBySlot.get(presentation.action) ?? null)

  const idleClip = catalog?.clipsBySlot.get('idle') ?? null
  const clip = targetClip ?? idleClip

  const preparationForClip =
    presentation.kind === 'base' &&
    peekPreparation?.action === presentation.action &&
    peekPreparation.packId === catalog?.packId &&
    clip?.system_slot === peekPreparation.action
      ? peekPreparation
      : null

  // 包、素材版本和播放请求均参与切换键，同一路径的新请求也须重播。
  const clipSwitchKey = clip ? `${clip.video_ref}@${clip.asset_revision}` : 'none'
  const stableMountKey = `${presentation.mountKey}|${catalog?.packId ?? 0}|${clipSwitchKey}`
  const preparationKey = preparationForClip ? `|prepare:${preparationForClip.generation}` : ''
  const mountKey = `${stableMountKey}${preparationKey}`

  const prevInstanceRef = useRef<ActionPlayInstance | null>(null)
  useEffect(() => {
    const currentInstance =
      presentation.kind === 'expression' ? presentation.instance : deferExpressionForPeek ? playInstance : null

    if (currentInstance !== prevInstanceRef.current) {
      settleReplacedInstance(prevInstanceRef.current)
    }

    prevInstanceRef.current = currentInstance
  }, [deferExpressionForPeek, playInstance, presentation])

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

        el.loop = loop
        const [, hitmask] = await Promise.all([loadVideo(el, url, controller.signal), resolveHitmask(clip)])

        if (controller.signal.aborted) {
          return
        }

        // 表达实例从头播放；真实可见后才上报 started（备用播放器预热不计）。
        if (instance !== null) {
          if (!(await prepareFirstFrame(el, controller.signal))) {
            throw new Error('First video frame unavailable')
          }

          if (!shouldStartVisibleInstance(instance)) {
            return
          }
        }

        const showFirstFrame = (): void => {
          const previous = front.current
          front.current = slot
          mounted.current = {
            key: preparationForClip ? stableMountKey : mountKey,
            playId: instance?.playId ?? null
          }
          hitmaskRef.current = hitmask
          // 遮挡与播放器同一帧提交，不把淡出的完整身体套进探身蒙版。
          flushSync(() => {
            setVisible(slot)
            setVisibleClip(clip)
          })

          if (instance !== null) {
            window.requestAnimationFrame(() => {
              if (
                front.current === slot &&
                mounted.current?.playId === instance.playId &&
                shouldStartVisibleInstance(instance)
              ) {
                void reportReceipt({ play_id: instance.playId }, 'started')
              }
            })
          }

          pauseTimer = window.setTimeout(() => {
            if (previous !== null && front.current !== previous) {
              elements[previous]?.pause()
            }
          }, 140)
        }

        if (preparationForClip) {
          if (
            !(await commitPeekPreparation(
              preparationForClip.action,
              preparationForClip.generation,
              () => prepareFirstFrame(el, controller.signal),
              showFirstFrame
            ))
          ) {
            if (!controller.signal.aborted) {
              el.pause()
            }
          }
        } else {
          showFirstFrame()
        }
      } catch (error) {
        if (controller.signal.aborted) {
          return
        }

        log.warn('video-stage', 'Could not play action', error)

        if (instance !== null) {
          void reportReceipt({ play_id: instance.playId }, 'rejected', 'load failed')
          finishPlayInstance(instance.generation)
        } else if (preparationForClip) {
          cancelPeekPreparation(preparationForClip.action, preparationForClip.generation)
        } else if (front.current === null) {
          // 首帧失败由外层回落；已有画面则继续保留。
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
  }, [catalog, clip, mountKey, preparationForClip, presentation, stableMountKey])

  useEffect(() => {
    probeInteractiveRegions()
  }, [visible, spatialPeek])

  // el.loop 不触发 ended，按 currentTime 回绕统计轮数。
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

    let lastTime = 0

    const handleTimeUpdate = (event: Event): void => {
      const el = front.current === null ? null : elements[front.current]

      if (!el || event.currentTarget !== el || mounted.current?.playId !== instance.playId) {
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

  // 只接收当前可见实例的结束事件。
  useEffect(() => {
    const elements = videos.current
    const instance = presentation.kind === 'expression' ? presentation.instance : null
    const playId = instance?.playId ?? ''

    const handleEnded = (event: Event): void => {
      const current = mounted.current

      if (!current || !instance || front.current === null || event.currentTarget !== elements[front.current]) {
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
    $spriteCanvasRect.set(canvasRect)
  }, [canvasRect])

  useEffect(() => {
    if (!canvas || !canvasRect) {
      $spriteContentRect.set(null)

      return
    }

    // 将素材轮廓映射到舞台；旧目录按整画布兜底。
    const drawW = canvasRect.right - canvasRect.left
    const drawH = canvasRect.bottom - canvasRect.top
    const bounds: readonly [number, number, number, number] = visibleClip?.content_rect ?? [0, 0, 1, 1]

    const contentRect = {
      left: canvasRect.left + bounds[0] * drawW,
      top: canvasRect.top + bounds[1] * drawH,
      right: canvasRect.left + bounds[2] * drawW,
      bottom: canvasRect.top + bounds[3] * drawH
    }

    $spriteContentRect.set(contentRect)
  }, [canvas, canvasRect, visibleClip])

  useEffect(
    () => () => {
      $spriteCanvasRect.set(null)
      $spriteContentRect.set(null)
    },
    []
  )

  useEffect(() => {
    $videoHitTest.set((px, py) => {
      const el = front.current === null ? null : videos.current[front.current]
      const hitmask = hitmaskRef.current
      const rect = rootRef.current?.getBoundingClientRect()

      // 遮挡先于 alpha 缺失回退，隐藏区域始终穿透。
      const pos = $spatialPos.get()
      const stageScale = $spatialScale.get()
      const masked = peekMaskRects($spatialPeek.get(), pos, stageScale, getBaseSpriteWidth(), getBaseSpriteHeight())
      const localX = (px - pos.x) / stageScale
      const localY = (py - pos.y) / stageScale

      if (
        masked.some(
          block => localX >= block.left && localX < block.right && localY >= block.top && localY < block.bottom
        )
      ) {
        return false
      }

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
          style={{
            opacity: visible === slot ? 1 : 0,
            transition: spatialPeek || peekPreparation ? 'none' : 'opacity 120ms linear'
          }}
        />
      ))}
    </div>
  )
}
