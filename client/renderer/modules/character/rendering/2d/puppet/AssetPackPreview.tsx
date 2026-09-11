import { useCallback, useEffect, useRef, useState } from 'react'

import { log } from '@/shared/lib/log'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, INPUT_CLASS } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { fetchPsdWithCache } from '../mesh2d/psd-opfs-cache'

import { ACTION_IMPULSE_DEFAULTS, ACTION_SMOOTHED_DEFAULTS, ACTIONS } from './actions'
import { loadPuppetAssetPack, type PuppetAssetPack, type PuppetAssetSource } from './asset-pack'
import { EdgePoseCanvas, type EdgePoseCanvasHandle } from './EdgePoseCanvas'
import { loadPsdIntoRuntime, PuppetRuntime } from './puppet-runtime'

type Mode = 'front' | 'left' | 'right'
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

export function AssetPackPreview({
  source,
  imageUrl
}: {
  source: PuppetAssetSource
  imageUrl: string | null
}): React.JSX.Element {
  const t = useStrings().living.wardrobe.preview
  const [mode, setMode] = useState<Mode>('front')
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

        instance = new PuppetRuntime(canvas)
        await loadPsdIntoRuntime(instance, buffer)

        if (controller.signal.aborted) {
          return
        }

        instance.auto.talk = false
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
        mode === 'front' ? 'none' : mode,
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
  }, [action, mode, playing, front, edges, pack, restart])

  const status = mode === 'front' ? front : pack?.poses ? edges[mode] : front === 'loading' ? 'loading' : 'failed'

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
        className="relative min-h-0 flex-1 overflow-hidden rounded-xl border border-line-strong bg-fill-trough"
        role="region"
      >
        {imageUrl && status !== 'ready' && (
          <img alt="" className="absolute inset-0 h-full w-full object-contain opacity-40" src={imageUrl} />
        )}
        <div
          className="absolute inset-3 flex items-center justify-center"
          ref={mount}
          style={{ visibility: mode === 'front' && front === 'ready' ? 'visible' : 'hidden' }}
        />
        {pack?.poses && (
          <EdgePoseCanvas boundary="container" key={retry} onStatus={onEdgeStatus} pack={pack.poses} ref={edge} />
        )}
        {mode !== 'front' && (
          <div
            className={cn(
              'pointer-events-none absolute inset-y-0 w-1 bg-accent-line',
              mode === 'left' ? 'left-0' : 'right-0'
            )}
          />
        )}
        {status !== 'ready' && (
          <div
            className="absolute inset-x-3 bottom-3 rounded-lg bg-surface-card p-2 text-center text-xs text-body"
            role="status"
          >
            {status === 'loading' ? t.loading : mode !== 'front' && pack && !pack.poses ? t.noPose : t.failed}
            {status === 'failed' && !(mode !== 'front' && pack && !pack.poses) && (
              <button className={BTN_GHOST} onClick={() => setRetry(value => value + 1)} type="button">
                {t.retry}
              </button>
            )}
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {mode === 'front' && (
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
