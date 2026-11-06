import type { ReactNode } from 'react'

import { backendDetailMessage } from '@/shared/lib/ipc-error'
import { useStrings } from '@/shared/strings'

import { EmptyState, LoadingBlock } from './components'
import { BTN_SUBTLE } from './palette'

/**
 * 列表区的加载/失败/内容三态；失败必带重试。
 * 失败标题必须是失败描述，不是字段名。
 */
export function AsyncSection({
  children,
  error,
  errorTitle,
  isLoading,
  loadingLabel,
  onRetry,
  retryLabel
}: {
  children: ReactNode
  error: unknown
  errorTitle: string
  isLoading: boolean
  loadingLabel: string
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
        description={backendDetailMessage(error, errorTitle)}
        title={errorTitle}
      />
    )
  }

  return <>{children}</>
}
