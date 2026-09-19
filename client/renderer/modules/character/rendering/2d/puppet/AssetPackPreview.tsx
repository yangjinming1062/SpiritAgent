import { useCallback, useEffect, useRef, useState } from 'react'

import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, INPUT_CLASS } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { fetchPsdWithCache } from '../mesh2d/psd-opfs-cache'

import { ACTION_IMPULSE_DEFAULTS, ACTION_SMOOTHED_DEFAULTS, ACTIONS } from './actions'
import { loadPuppetAssetPack, type PuppetAssetPack, type PuppetAssetSource } from './asset-pack'
import type { EdgePose } from './edge-pose'
import { EdgePoseCanvas, type EdgePoseCanvasHandle } from './EdgePoseCanvas'
import { loadPsdIntoRuntime, PuppetRuntime } from './puppet-runtime'

type Mode = 'front' | 'left' | 'right'
type PoseView = 'edge' | 'full'
type LoadState = 'loading' | 'ready' | 'failed'

const PREVIEW_ACTIONS = [
  'idle',
  'wave_left',
  'wave_right',
  'look_away_left',
  'look_away_right',
  'turn_body_left',
  'turn_body_right',
  'petting'
] as const

interface PoseRegenView {
  side: 'left' | 'right'
  error: string | null
}

export function AssetPackPreview({
  onRegeneratePose,
  onSelfSourcePose,
  poseRegen,
  source
}: {
  onRegeneratePose: (side: 'left' | 'right') => void
  /** 提供时在姿态重绘旁展示「使用自己的图」入口（自备图采纳）。 */
  onSelfSourcePose?: (side: 'left' | 'right') => void
  poseRegen: PoseRegenView | null
  source: PuppetAssetSource
}): React.JSX.Element {
  const t = useStrings().living.wardrobe.preview
  const selfSource = useStrings().selfSource
  const [mode, setMode] = useState<Mode>('front')
  const [poseView, setPoseView] = useState<PoseView>('edge')
  const [darkBackdrop, setDarkBackdrop] = useState(false)
  const [debugMarks, setDebugMarks] = useState(false)
  const [action, setAction] = useState<string>('idle')
  const [playing, setPlaying] = useState(() => !window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const [restart, setRestart] = useState(0)
  const [retry, setRetry] = useState(0)
  const [pack, setPack] = useState<PuppetAssetPack | null>(null)
  const [front, setFront] = useState<LoadState>('loading')
  const [edges, setEdges] = useState<Record<'left' | 'right', LoadState>>({ left: 'loading', right: 'loading' })
  const mount = useRef<HTMLDivElement>(null)
  const runtime = useRef<PuppetRuntime | null>(null)
  const edge = useRef<EdgePoseCanvasHandle>(null)
  const elapsed = useRef(0)

  const onEdgeStatus = useCallback((side: 'left' | 'right', status: 'ready' | 'failed'): void => {
    setEdges(current => ({ ...current, [side]: status }))
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const canvas = document.createElement('canvas')
    canvas.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain'
    mount.current?.append(canvas)
    let instance: PuppetRuntime | null = null
    setPack(null)
    setFront('loading')
    setEdges({ left: 'loading', right: 'loading' })

    void (async (): Promise<void> => {
      try {
        const loaded = await loadPuppetAssetPack(source)

        if (controller.signal.aborted) {
          return
        }

        setPack(loaded)
        const buffer = await fetchPsdWithCache(loaded.psdUrl, loaded.contentHash, controller.signal)

        if (controller.signal.aborted) {
          return
        }

        instance = new PuppetRuntime(canvas, error => {
          log.warn('wardrobe-preview', 'WebGL recovery failed', error)
          setFront('failed')
        })

        await loadPsdIntoRuntime(instance, buffer)

        if (controller.signal.aborted) {
          return
        }

        instance.auto.rand = false
        instance.auto.gaze = false
        instance.renderFrame(1 / 60)
        runtime.current = instance
        setFront('ready')
      } catch (error) {
        if (!controller.signal.aborted) {
          instance?.dispose()
          instance = null
          log.warn('wardrobe-preview', 'asset loading failed', error)
          setFront('failed')
        }
      }
    })()

    return (): void => {
      controller.abort()
      runtime.current = null
      instance?.dispose()
      canvas.remove()
    }
  }, [source, retry])

  useEffect(() => {
    elapsed.current = 0
    const rt = runtime.current

    if (rt) {
      Object.assign(rt.target, ACTION_IMPULSE_DEFAULTS, ACTION_SMOOTHED_DEFAULTS, { armPos: 0, armY: 0 })
    }
  }, [action, mode, restart, front])

  useEffect(() => {
    let raf = 0
    let last = performance.now()

    const draw = (now: number): void => {
      cancelAnimationFrame(raf)

      if (document.hidden) {
        last = now

        return
      }

      const dt = playing ? Math.min(0.05, Math.max(0, (now - last) / 1000)) : 0
      last = now
      elapsed.current += dt * 1000
      const rt = runtime.current

      if (mode === 'front' && rt && front === 'ready') {
        const envelope = ACTIONS[action]

        if (envelope) {
          const phase = elapsed.current % (envelope.durMs + 600)

          if (phase <= envelope.durMs) {
            envelope.apply(phase / envelope.durMs, rt)
          } else {
            Object.assign(rt.target, ACTION_IMPULSE_DEFAULTS, ACTION_SMOOTHED_DEFAULTS, { armPos: 0, armY: 0 })
          }
        }

        rt.renderFrame(dt)
      }

      edge.current?.update(
        mode === 'front' || poseView === 'full' ? 'none' : mode,
        dt,
        elapsed.current,
        Math.sin(elapsed.current / 1800) * 0.35,
        false
      )

      if (playing) {
        raf = requestAnimationFrame(draw)
      }
    }

    const redraw = (): void => {
      last = performance.now()
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(draw)
    }

    const observer = new ResizeObserver(redraw)

    if (mount.current) {
      observer.observe(mount.current)
    }

    document.addEventListener('visibilitychange', redraw)
    redraw()

    return (): void => {
      cancelAnimationFrame(raf)
      observer.disconnect()
      document.removeEventListener('visibilitychange', redraw)
    }
  }, [action, mode, poseView, playing, front, edges, pack, restart])

  const status = mode === 'front' ? front : pack?.poses ? edges[mode] : front === 'loading' ? 'loading' : 'failed'
  const regenBusy = poseRegen !== null && poseRegen.error === null

  const regenNotice =
    mode !== 'front' && poseRegen !== null && poseRegen.side === mode
      ? poseRegen.error === null
        ? t.regenRunning
        : poseRegen.error
          ? `${t.regenFailed}: ${poseRegen.error}`
          : t.regenFailed
      : null

  return (
    <div className="absolute inset-4 flex min-h-0 flex-col gap-2">
      <div aria-label={t.views} className="flex flex-wrap items-center gap-1">
        {(['front', 'left', 'right'] as const).map(view => (
          <button
            aria-pressed={mode === view}
            className={cn(BTN_GHOST, mode === view && 'bg-accent-soft text-strong')}
            key={view}
            onClick={() => setMode(view)}
            type="button"
          >
            {t[view]}
          </button>
        ))}
      </div>
      <div
        aria-label={t.stage}
        className={cn(
          'relative min-h-0 flex-1 overflow-hidden rounded-xl border border-line-strong',
          mode !== 'front' ? (darkBackdrop ? 'bg-neutral-900' : 'bg-neutral-100') : 'bg-fill-trough'
        )}
        role="region"
      >
        <div
          className="absolute inset-3 flex items-center justify-center"
          ref={mount}
          style={{ visibility: mode === 'front' && front === 'ready' ? 'visible' : 'hidden' }}
        />
        {pack?.poses && (
          <EdgePoseCanvas boundary="container" key={retry} onStatus={onEdgeStatus} pack={pack.poses} ref={edge} />
        )}
        {mode !== 'front' && pack?.poses && poseView === 'full' && (
          <FullPoseView debug={debugMarks} pose={pack.poses[mode]} />
        )}
        {mode !== 'front' && poseView === 'edge' && (
          <div
            className={cn(
              'pointer-events-none absolute inset-y-0 w-1 bg-accent-line',
              mode === 'left' ? 'left-0' : 'right-0'
            )}
          />
        )}
        {(status !== 'ready' || regenNotice) && (
          <div
            className="absolute inset-x-3 bottom-3 rounded-lg bg-surface-card p-2 text-center text-xs text-body"
            role="status"
          >
            {status !== 'ready' ? (
              <>
                {status === 'loading' ? t.loading : mode !== 'front' && pack && !pack.poses ? t.noPose : t.failed}
                {status === 'failed' && !(mode !== 'front' && pack && !pack.poses) && (
                  <button className={BTN_GHOST} onClick={() => setRetry(value => value + 1)} type="button">
                    {t.retry}
                  </button>
                )}
              </>
            ) : (
              regenNotice
            )}
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {mode === 'front' ? (
          <select
            aria-label={t.action}
            className={cn(INPUT_CLASS, 'min-w-0 flex-1')}
            onChange={event => setAction(event.target.value)}
            value={action}
          >
            {PREVIEW_ACTIONS.map(value => (
              <option key={value} value={value}>
                {t.actions[value]}
              </option>
            ))}
          </select>
        ) : (
          <>
            <button
              className={BTN_GHOST}
              disabled={regenBusy}
              onClick={() => onRegeneratePose(mode)}
              title={t.regenPose}
              type="button"
            >
              {regenBusy && poseRegen?.side === mode ? t.regenRunning : t.regenPose}
            </button>
            {onSelfSourcePose && (
              <button
                className={BTN_GHOST}
                disabled={regenBusy}
                onClick={() => onSelfSourcePose(mode)}
                title={selfSource.openTitle}
                type="button"
              >
                {selfSource.open}
              </button>
            )}
            <ToggleGroup
              items={
                [
                  ['edge', t.edgeEffect],
                  ['full', t.fullAsset]
                ] as const
              }
              onSelect={setPoseView}
              value={poseView}
            />
            <ToggleGroup
              items={
                [
                  [false, t.backdropLight],
                  [true, t.backdropDark]
                ] as const
              }
              onSelect={setDarkBackdrop}
              value={darkBackdrop}
            />
            {poseView === 'full' && (
              <button
                aria-pressed={debugMarks}
                className={cn(BTN_GHOST, debugMarks && 'bg-accent-soft text-strong')}
                onClick={() => setDebugMarks(value => !value)}
                title={t.debugMarks}
                type="button"
              >
                {t.debugMarks}
              </button>
            )}
          </>
        )}
        <button
          className={BTN_GHOST}
          disabled={status !== 'ready'}
          onClick={() => setPlaying(value => !value)}
          type="button"
        >
          {playing ? t.pause : t.play}
        </button>
        <button
          className={BTN_GHOST}
          disabled={status !== 'ready'}
          onClick={() => {
            setRestart(value => value + 1)
            setPlaying(true)
          }}
          type="button"
        >
          {t.replay}
        </button>
        <span className="text-[10px] text-faint">{t.loop}</span>
      </div>
    </div>
  )
}

/** 完整素材视图：按原始纹理等比展示全身构图与透明轮廓，不做贴边形变。
 *  接触线、头部框和手部框只是界面覆盖层，不导出、不回传生图模型。 */
function FullPoseView({ debug, pose }: { debug: boolean; pose: EdgePose }): React.JSX.Element {
  const t = useStrings().living.wardrobe.preview
  const containerRef = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState<{ height: number; width: number } | null>(null)
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const el = containerRef.current

    if (!el) {
      return undefined
    }

    const measure = (): void => {
      const scale = Math.min(el.clientWidth / pose.width, el.clientHeight / pose.height)

      if (Number.isFinite(scale) && scale > 0) {
        setBox({ height: Math.floor(pose.height * scale), width: Math.floor(pose.width * scale) })
      }
    }

    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)

    return () => observer.disconnect()
  }, [pose])

  useEffect(() => {
    let live = true
    let objectUrl: string | null = null
    setUrl(null)
    setFailed(false)

    void (async (): Promise<void> => {
      try {
        const bytes = await window.spiritagent.apiAssetBuffer({
          url: pose.textures.body.url,
          contentHash: pose.textures.body.hash
        })

        objectUrl = URL.createObjectURL(new Blob([bytes.slice().buffer], { type: 'image/webp' }))

        if (live) {
          setUrl(objectUrl)
        } else {
          URL.revokeObjectURL(objectUrl)
        }
      } catch {
        if (live) {
          setFailed(true)
        }
      }
    })()

    return (): void => {
      live = false

      if (objectUrl) {
        URL.revokeObjectURL(objectUrl)
      }
    }
  }, [pose])

  // 覆盖层以素材坐标的百分比定位，与显示缩放无关。
  const rectStyle = (rect: [number, number, number, number]): React.CSSProperties => ({
    left: `${(rect[0] / pose.width) * 100}%`,
    top: `${(rect[1] / pose.height) * 100}%`,
    width: `${((rect[2] - rect[0]) / pose.width) * 100}%`,
    height: `${((rect[3] - rect[1]) / pose.height) * 100}%`
  })

  return (
    <div className={cn('absolute inset-3 flex items-center justify-center')} ref={containerRef}>
      {box && url && (
        <div className="relative" style={{ height: box.height, width: box.width }}>
          <img alt="" className="block size-full" src={url} />
          {debug && (
            <>
              <div
                className="absolute inset-y-0 w-px bg-sky-400"
                style={{ left: `${(pose.contactX / pose.width) * 100}%` }}
              />
              <div className="absolute border border-amber-400" style={rectStyle(pose.head)} />
              {pose.hands?.map((hand, index) => (
                <div className="absolute border border-emerald-400" key={index} style={rectStyle(hand)} />
              ))}
            </>
          )}
        </div>
      )}
      {failed && <p className="rounded-lg bg-surface-card px-2 py-1 text-xs text-body">{t.fullAssetFailed}</p>}
    </div>
  )
}

/** 互斥切换按钮组：选中项高亮并用 aria-pressed 暴露选中态。 */
function ToggleGroup<T extends boolean | string>({
  items,
  onSelect,
  value
}: {
  items: readonly (readonly [T, string])[]
  onSelect: (value: T) => void
  value: T
}): React.JSX.Element {
  return (
    <>
      {items.map(([item, label]) => (
        <button
          aria-pressed={value === item}
          className={cn(BTN_GHOST, value === item && 'bg-accent-soft text-strong')}
          key={label}
          onClick={() => onSelect(item)}
          type="button"
        >
          {label}
        </button>
      ))}
    </>
  )
}
