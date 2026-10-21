import { cn } from '@/shared/lib/utils'

import { BTN_COMPACT } from './palette'

export interface ActionRowItem {
  label: string
  onClick: () => void
  disabled?: boolean
  /** 覆盖默认的小号描边按钮样式。 */
  className?: string
}

/** 一行操作按钮。用于媒体卡、复核队列与恢复列表里的并列动作。 */
export function ActionRow({ actions, className }: { actions: ActionRowItem[]; className?: string }): React.JSX.Element {
  return (
    <div className={cn('flex flex-wrap gap-2', className)}>
      {actions.map(action => (
        <button
          className={cn(BTN_COMPACT, action.className)}
          disabled={action.disabled}
          key={action.label}
          onClick={action.onClick}
          type="button"
        >
          {action.label}
        </button>
      ))}
    </div>
  )
}
