import type { ReactNode } from 'react'
import { useRef } from 'react'

import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'

import { PanelHeader } from './components'

interface WizardModalProps {
  regionId: string
  title: ReactNode
  onClose: () => void
  children: ReactNode
  // 固定在底部的操作条（上一步/下一步等），不随正文滚动。
  footer?: ReactNode
  // 内部还有自己的 Esc 语义（如灯箱打开时不关向导）时关闭默认 Esc 处理。
  escClose?: boolean
  widthClass?: string
}

// 全屏 rect 在挂载后不再变化，提到模块层避免每次 render 重建函数与对象。
const fullscreenRect = (): DOMRect => new DOMRect(0, 0, window.innerWidth, window.innerHeight)

// 伙伴窗线性向导的模态外壳：暗化背板 + 居中卡片。Esc 在捕获阶段拦截并阻断
// 冒泡——外层 FloatingPanel 的 Esc 处理器不会连坐关闭整个设置面板。
export function WizardModal({
  regionId,
  title,
  onClose,
  children,
  footer,
  escClose = true,
  widthClass = 'max-w-md'
}: WizardModalProps): React.JSX.Element {
  const overlayRef = useRef<HTMLDivElement>(null)

  useInteractiveRegion(regionId, overlayRef, fullscreenRect)

  useEscapeKey(onClose, { enabled: escClose })

  return (
    <div
      className="pointer-events-auto fixed inset-0 z-[60] flex items-center justify-center bg-black/40 px-6 py-6 backdrop-blur-sm"
      ref={overlayRef}
    >
      <div
        className={`flex max-h-[85vh] w-full ${widthClass} flex-col overflow-hidden rounded-2xl border border-line-standard bg-surface-panel text-strong shadow-2xl`}
      >
        <PanelHeader onClose={onClose} title={title} />
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>
        {footer && <div className="flex items-center gap-2 border-t border-line-hairline px-4 py-3">{footer}</div>}
      </div>
    </div>
  )
}
