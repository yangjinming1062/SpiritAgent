import type React from 'react'
import { X } from 'lucide-react'
import { getCurrentWindow } from '@tauri-apps/api/window'

// 安装器自定义标题栏：`tauri.conf.json` 设了 `decorations: false` 关掉系统 chrome，
// 这里画 cyber-glass 风格的标题栏替代。整条 `data-tauri-drag-region` 让用户能拖窗口；
// 关闭按钮用 `pointer-events-auto` 抢回点击，避免被 drag region 吃掉。
export function Titlebar(): React.JSX.Element {
  return (
    <div
      data-tauri-drag-region
      className="relative z-20 flex shrink-0 items-center justify-between border-b border-line-hairline bg-glass px-4 py-2 backdrop-blur-xs select-none"
    >
      {/* 左侧：品牌 + 状态文案（状态由 App.tsx 注入；这里只占位） */}
      <div className="flex items-center gap-2">
        <span className="font-['Collapse'] text-sm font-bold tracking-[0.08em] text-accent">
          SPIRITAGENT
        </span>
        <span className="text-xs text-text-faint">|</span>
        <span className="text-xs text-text-body">安装器</span>
      </div>

      {/* 右侧：关闭按钮 */}
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
