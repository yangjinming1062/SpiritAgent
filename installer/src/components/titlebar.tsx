import type React from 'react'
import { X } from 'lucide-react'
import { getCurrentWindow } from '@tauri-apps/api/window'

// 关闭按钮 pointer-events-auto，避开 drag region
export function Titlebar(): React.JSX.Element {
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
      </div>

      <button
        type="button"
        onClick={() => void getCurrentWindow().close()}
        className="pointer-events-auto inline-flex size-7 items-center justify-center rounded-md text-text-muted transition-colors hover:bg-destructive/20 hover:text-destructive"
        aria-label="关闭安装器"
      >
        <X size={16} />
      </button>
    </div>
  )
}
