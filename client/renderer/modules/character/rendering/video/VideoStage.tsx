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

import type { VideoClipSpec } from './types'
import { $videoPack, type ActiveVideoPack, resolveVideoClipUrl } from './video-pack-store'

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
      raf = window.setTimeout(tick, 120) as unknown as number
    }

    tick()

    return () => window.clearTimeout(raf)
  }, [])

  return action
}

function clipFor(pack: ActiveVideoPack, action: VideoActionKey): VideoClipSpec {
  const available = new Set(pack.manifest.clips.map(c => c.action))
  const wanted = pickAvailableClip(action, available, pack.manifest.default_action as VideoActionKey)

  return (
    pack.manifest.clips.find(c => c.action === wanted) ??
    pack.manifest.clips.find(c => c.action === pack.manifest.default_action) ??
    pack.manifest.clips[0]
  )
}

export function VideoStage(): React.JSX.Element {
  const pack = useStore($videoPack)
  const action = useCurrentAction()
  const [frontUrl, setFrontUrl] = useState<string | null>(null)
  const [backUrl, setBackUrl] = useState<string | null>(null)
  const frontRef = useRef<HTMLVideoElement>(null)
  const backRef = useRef<HTMLVideoElement>(null)
  const currentClipRef = useRef<VideoClipSpec | null>(null)

  const packRef = useRef<ActiveVideoPack | null>(pack)
  packRef.current = pack

  const canvas = pack?.manifest.canvas

  // 动作变化 → 预加载到备用 video，可播放首帧后与前台交换（避免切换黑帧/闪烁）。
  useEffect(() => {
    if (!pack) {
      setFrontUrl(null)
      setBackUrl(null)

      return
    }

    let cancelled = false
    const clip = clipFor(pack, action)
    currentClipRef.current = clip

    void (async () => {
      const url = await resolveVideoClipUrl(pack, clip.action)

      if (cancelled || !url) {
        return
      }

      setBackUrl(url)
      const el = backRef.current

      if (el) {
        el.currentTime = 0
        await el.play().catch(() => undefined)
        // 就绪后交换：备用成为前台
        setFrontUrl(url)
        setBackUrl(null)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [pack, action])

  // 内容包围盒：以画布比例整体上报（视频片段自带透明留白，alpha 精化由命中遮罩负责）。
  useEffect(() => {
    if (!canvas) {
      return
    }

    $spriteContentRect.set({
      left: 0,
      top: 0,
      right: canvas.width,
      bottom: canvas.height
    })

    return () => {
      $spriteContentRect.set(null)
    }
  }, [canvas])

  // 命中探测登记：遮罩网格取时间上最接近的采样；坐标为舞台像素，
  // 经组件自身包围盒（已含容器变换）归一化后映射到网格行列。
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    $videoHitTest.set((px, py) => {
      const clip = currentClipRef.current
      const grid = clip?.hitmask_grid
      const rect = rootRef.current?.getBoundingClientRect()

      if (!clip || !grid || clip.hitmask.length === 0 || !rect || rect.width <= 0 || rect.height <= 0) {
        return null
      }

      const [gw, gh] = grid
      const nx = (px - rect.left) / rect.width
      const ny = (py - rect.top) / rect.height

      if (nx < 0 || nx > 1 || ny < 0 || ny > 1) {
        return false
      }

      const col = Math.min(gw - 1, Math.floor(nx * gw))
      const row = Math.min(gh - 1, Math.floor(ny * gh))
      const sample = clip.hitmask[Math.min(clip.hitmask.length - 1, Math.floor(clip.hitmask.length / 2))]

      return ((sample[row] ?? 0) & (1 << col)) !== 0
    })

    return () => {
      $videoHitTest.set(null)
    }
  }, [])

  if (!pack || !canvas || !frontUrl) {
    return <div className="h-full w-full" />
  }

  return (
    <div className="relative h-full w-full" ref={rootRef} style={{ aspectRatio: `${canvas.width} / ${canvas.height}` }}>
      <video
        autoPlay
        className="h-full w-full object-contain"
        loop
        muted
        playsInline
        ref={frontRef}
        src={frontUrl ?? undefined}
      />
      <video
        className="hidden h-full w-full object-contain"
        muted
        playsInline
        ref={backRef}
        src={backUrl ?? undefined}
      />
    </div>
  )
}

// 动作覆盖（预留）：显式覆盖优先于状态解析；视频链不驱动嘴部或视线。
export const $videoActionOverride = atom<string | null>(null)
