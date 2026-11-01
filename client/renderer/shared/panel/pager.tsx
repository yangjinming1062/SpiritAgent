import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

import { Spinner } from './components'
import { BTN_SUBTLE } from './palette'

/**
 * 统一分页控件。
 * - `pages`：上一页/下一页 + 页码（可带总数），用于卡片网格类列表；
 * - `more`：单个「加载更多」按钮，用于时间流类列表。
 * 单页且无更多时返回 null，调用方无需自行判断。
 */
export function Pager({
  mode = 'pages',
  page,
  hasMore,
  busy = false,
  disabled = false,
  totalPages = null,
  total = null,
  onPageChange,
  onLoadMore,
  className
}: {
  mode?: 'pages' | 'more'
  /** pages 模式的当前页（0 起）；more 模式不传。 */
  page?: number
  hasMore: boolean
  busy?: boolean
  disabled?: boolean
  /** 总页数；页大小归调用方，由其计算。 */
  totalPages?: number | null
  /** 总条目数，仅在 pages 模式展示。 */
  total?: number | null
  onPageChange?: (next: number) => void
  onLoadMore?: () => void
  className?: string
}): React.JSX.Element | null {
  const t = useStrings()

  if (mode === 'more') {
    if (!hasMore) {
      return null
    }

    return (
      <button
        className={cn(BTN_SUBTLE, 'mx-auto flex', className)}
        disabled={busy || disabled}
        onClick={() => onLoadMore?.()}
        type="button"
      >
        {busy ? <Spinner size="sm" /> : null}
        {t.common.loadMore}
      </button>
    )
  }

  const current = page ?? 0

  if (current === 0 && !hasMore) {
    return null
  }

  return (
    <div className={cn('flex items-center justify-between gap-3', className)}>
      <button
        className={BTN_SUBTLE}
        disabled={busy || disabled || current === 0}
        onClick={() => onPageChange?.(current - 1)}
        type="button"
      >
        {t.common.previous}
      </button>
      <span aria-live="polite" className="text-[11px] text-faint">
        {totalPages !== null
          ? t.common.pageOf(current + 1, totalPages)
          : `${current + 1}`}
        {total !== null ? ` · ${t.common.totalItems(total)}` : ''}
      </span>
      <button
        className={BTN_SUBTLE}
        disabled={busy || disabled || !hasMore}
        onClick={() => onPageChange?.(current + 1)}
        type="button"
      >
        {t.common.next}
      </button>
    </div>
  )
}
