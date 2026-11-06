import type { AutoSaveStatus } from '@/shared/hooks/use-auto-save'

import { BTN_SUBTLE, HINT_TEXT } from './palette'

/**
 * 自动保存的页脚状态：保存中、已保存、失败并可重试。
 * 状态由 `useAutoSave` 判定，本组件只呈现，不发起请求也不推测进度。
 */
export function SaveStatus({
  status,
  savingLabel,
  savedLabel,
  errorLabel,
  retryLabel,
  onRetry
}: {
  status: AutoSaveStatus
  savingLabel: string
  savedLabel: string
  errorLabel: string
  retryLabel: string
  onRetry: () => void
}): React.JSX.Element {
  return (
    <div className="flex min-h-5 items-center justify-end gap-3 pt-2">
      {status === 'saving' && <span className={HINT_TEXT}>{savingLabel}</span>}
      {status === 'saved' && <span className={HINT_TEXT}>{savedLabel}</span>}
      {status === 'error' && (
        <>
          <span className="text-xs leading-relaxed text-danger-fg">{errorLabel}</span>
          <button className={BTN_SUBTLE} onClick={onRetry} type="button">
            {retryLabel}
          </button>
        </>
      )}
    </div>
  )
}
