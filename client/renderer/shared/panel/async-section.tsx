import type { ReactNode } from 'react'

import type { IconComponent } from '@/shared/lib/icons'
import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { useStrings } from '@/shared/strings'

import { EmptyState, LoadingBlock } from './components'
import { BTN_SUBTLE } from './palette'

/**
 * 列表区的统一四态：加载中 → 失败（必带重试）→ 空态 → 内容。
 * 空态标题/描述由业务给出；失败标题必须是失败描述，不是字段名。
 */
export function AsyncSection({
  children,
  error,
  errorDescription,
  errorTitle,
  isLoading,
  loadingLabel,
  isEmpty = false,
  emptyTitle,
  emptyDescription,
  emptyAction,
  emptyIcon,
  onRetry,
  retryLabel
}: {
  children: ReactNode
  error: unknown
  errorDescription?: string
  errorTitle: string
  isLoading: boolean
  loadingLabel: string
  isEmpty?: boolean
  emptyTitle?: string
  emptyDescription?: string
  emptyAction?: ReactNode
  emptyIcon?: IconComponent
  onRetry: () => void
  retryLabel?: string
}): React.JSX.Element {
  const t = useStrings()

  if (isLoading) {
    return <LoadingBlock label={loadingLabel} />
  }

  if (error) {
    return (
      <EmptyState
        action={
          <button className={BTN_SUBTLE} onClick={onRetry} type="button">
            {retryLabel ?? t.common.retry}
          </button>
        }
        description={errorDescription ?? backendDetailMessage(error, errorTitle)}
        title={errorTitle}
      />
    )
  }

  if (isEmpty) {
    return <EmptyState action={emptyAction} description={emptyDescription} icon={emptyIcon} title={emptyTitle ?? ''} />
  }

  return <>{children}</>
}
