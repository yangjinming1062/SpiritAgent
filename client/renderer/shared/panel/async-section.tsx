import type { ReactNode } from 'react'

import { backendDetailMessage } from '@/shared/lib/ipc-error'

import { EmptyState, LoadingBlock } from './components'
import { BTN_SUBTLE } from './palette'

/**
 * 列表区的统一三态：加载中 → 失败（必带重试）→ 内容。
 * 空态由调用方按自己的业务条件给出；失败标题必须是失败描述，不是字段名。
 */
export function AsyncSection({
  children,
  error,
  errorDescription,
  errorTitle,
  isLoading,
  loadingLabel,
  onRetry,
  retryLabel
}: {
  children: ReactNode
  error: unknown
  errorDescription?: string
  errorTitle: string
  isLoading: boolean
  loadingLabel: string
  onRetry: () => void
  retryLabel: string
}): React.JSX.Element {
  if (isLoading) {
    return <LoadingBlock label={loadingLabel} />
  }

  if (error) {
    return (
      <EmptyState
        action={
          <button className={BTN_SUBTLE} onClick={onRetry} type="button">
            {retryLabel}
          </button>
        }
        description={errorDescription ?? backendDetailMessage(error, errorTitle)}
        title={errorTitle}
      />
    )
  }

  return <>{children}</>
}
