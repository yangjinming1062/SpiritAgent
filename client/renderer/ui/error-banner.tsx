import { cn } from '@/shared/lib/utils'

import { BTN_GHOST } from './palette'

/**
 * 行内失败提示：文案加可选重试动作。用于与当前状态绑定的失败（加载失败、保存失败），
 * 动作型失败走 toast，不在这里重复呈现。
 *
 * `variant="bar"` 用于输入区上方的整行提示条，`inline` 用于卡片内的行内提示。
 */
export function ErrorBanner({
  message,
  detail,
  onRetry,
  retryLabel,
  tone = 'warning',
  variant = 'inline',
  className
}: {
  message: string
  /** 补充说明，展开在主文案下方。 */
  detail?: string
  onRetry?: () => void
  retryLabel?: string
  tone?: 'warning' | 'danger'
  variant?: 'inline' | 'bar'
  className?: string
}): React.JSX.Element | null {
  if (!message) {
    return null
  }

  return (
    <div
      className={cn(
        variant === 'bar'
          ? 'flex items-center gap-3 border-b border-line-hairline bg-[var(--ui-app-bg)] px-4 py-2.5 text-xs text-muted'
          : 'flex flex-wrap items-center gap-2',
        className
      )}
      role="alert"
    >
      <p
        aria-live="polite"
        className={cn('min-w-0 break-words', tone === 'danger' ? 'text-danger-fg' : 'text-amber-300/90')}
      >
        {message}
      </p>
      {detail ? <p className="text-[11px] leading-relaxed text-faint">{detail}</p> : null}
      {onRetry && retryLabel ? (
        <button className={BTN_GHOST} onClick={onRetry} type="button">
          {retryLabel}
        </button>
      ) : null}
    </div>
  )
}
