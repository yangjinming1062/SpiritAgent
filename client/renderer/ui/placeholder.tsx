import type { ReactNode } from 'react'

/**
 * 行内占位框：图片、提示词等区域在数据未就绪或为空时的统一呈现。
 * 与 `EmptyState` 的区别是本组件没有独立高度，适合放在已有边框的容器内。
 */
export function Placeholder({
  children,
  className = ''
}: {
  children: ReactNode
  className?: string
}): React.JSX.Element {
  return (
    <p
      className={`rounded-xl border border-line-hairline bg-fill-trough px-4 py-6 text-center text-xs text-muted ${className}`}
    >
      {children}
    </p>
  )
}
