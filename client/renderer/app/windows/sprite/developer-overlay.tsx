import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { $spriteEmotion, $spriteState } from '@/modules/character'
import { $engineFps, $powerProfile, $rendererBackend } from '@/modules/character/rendering/3d'
import { X } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'

import { $devLogs, $devMode } from '../../runtime/dev-log'

export function DeveloperOverlay(): React.JSX.Element | null {
  const isDev = useStore($devMode)
  const spriteState = useStore($spriteState)
  const emotion = useStore($spriteEmotion)
  const logs = useStore($devLogs)
  const backend = useStore($rendererBackend)
  const powerProfile = useStore($powerProfile)
  const fps = useStore($engineFps)
  const [minimized, setMinimized] = useState(false)
  const overlayRef = useRef<HTMLDivElement>(null)

  useInteractiveRegion('developer-overlay', overlayRef)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'D' || e.key === 'd')) {
        e.preventDefault()
        $devMode.set(!$devMode.get())
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (!isDev) {
    return null
  }

  return (
    <div
      className="fixed top-4 left-4 z-50 w-80 overflow-hidden rounded-xl border border-emerald-500/30 bg-black/85 font-mono text-[11px] text-emerald-400 shadow-2xl backdrop-blur-md select-none"
      ref={overlayRef}
    >
      <div className="flex items-center justify-between border-b border-emerald-500/20 bg-emerald-950/40 px-3 py-1.5">
        <span className="font-semibold text-emerald-300">Developer Debug Mode</span>
        <div className="flex items-center gap-2">
          <button className="text-emerald-400 hover:text-strong" onClick={() => setMinimized(!minimized)} type="button">
            {minimized ? '展开' : '折叠'}
          </button>
          <button
            aria-label="关闭调试面板"
            className="text-emerald-400 hover:text-strong"
            onClick={() => $devMode.set(false)}
            type="button"
          >
            <X className="size-3.5" />
          </button>
        </div>
      </div>

      {!minimized && (
        <div className="p-3 space-y-2">
          <div className="grid grid-cols-2 gap-1 rounded border border-emerald-500/20 bg-black/50 p-2 text-xs">
            <div>
              State: <span className="text-white font-bold">{spriteState}</span>
            </div>
            <div>
              Emotion: <span className="text-strong">{emotion || 'none'}</span>
            </div>
            <div>
              Render: <span className="text-white font-bold">{backend ?? '…'}</span>
            </div>
            <div>
              Power: <span className="text-white font-bold">{powerProfile}</span>{' '}
              <span className="text-emerald-600">({fps.toFixed(0)} fps)</span>
            </div>
          </div>

          <div className="h-44 overflow-y-auto space-y-1 rounded border border-emerald-500/20 bg-black/60 p-2">
            {logs.length === 0 && <p className="text-emerald-600 italic">No JSON-RPC frames captured yet…</p>}
            {logs.map((log, idx) => (
              <div className="leading-tight" key={idx}>
                <span className="text-emerald-600">[{log.time}]</span>{' '}
                <span className="font-semibold text-emerald-300">{log.type}:</span>{' '}
                <span className="text-body">{log.details}</span>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-emerald-600 text-right">按 Ctrl+Shift+D 随时开关</p>
        </div>
      )}
    </div>
  )
}
