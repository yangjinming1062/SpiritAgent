import type React from 'react'
import { useState } from 'react'
import { Loader2, X } from 'lucide-react'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { cancelInstall } from '../store'

// 关闭按钮 pointer-events-auto，避开 drag region
export function Titlebar(): React.JSX.Element {
  const [closing, setClosing] = useState(false)
  const [error, setError] = useState('')

  const close = async (): Promise<void> => {
    setClosing(true)
    setError('')
    try {
      await cancelInstall()
      await getCurrentWindow().close()
    } catch (failure) {
      setClosing(false)
      setError(failure instanceof Error ? failure.message : String(failure))
    }
  }

  return (
    <div
      data-tauri-drag-region
      className="relative z-20 flex shrink-0 items-center justify-between border-b border-line-hairline bg-glass px-4 py-2 backdrop-blur-xs select-none"
    >
      <div className="flex items-center gap-2">
        <span className="font-['Collapse'] text-sm font-bold tracking-[0.08em] text-accent">
          SPIRITAGENT
        </span>
        <span className="text-xs text-text-faint">|</span>
        <span className="text-xs text-text-body">安装器</span>
        {error ? <span className="text-xs text-destructive" role="alert" title={error}>无法关闭，请重试</span> : null}
      </div>

      <button
        type="button"
        disabled={closing}
        onClick={() => void close()}
        className="pointer-events-auto inline-flex size-7 items-center justify-center rounded-md text-text-muted transition-colors hover:bg-destructive/20 hover:text-destructive"
        aria-label={closing ? '正在停止并关闭安装器' : '关闭安装器'}
      >
        {closing ? <Loader2 className="animate-spin" size={16} /> : <X size={16} />}
      </button>
    </div>
  )
}
