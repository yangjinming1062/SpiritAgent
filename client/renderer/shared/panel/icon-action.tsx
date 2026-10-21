import { type IconComponent } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

/** 列表行尾的图标操作按钮。危险操作单独着色。 */
export function IconAction({
  icon: Icon,
  label,
  danger = false,
  onClick,
  className
}: {
  icon: IconComponent
  label: string
  danger?: boolean
  onClick: () => void
  className?: string
}): React.JSX.Element {
  return (
    <button
      aria-label={label}
      className={cn(
        'rounded-md p-1 transition-colors',
        danger
          ? 'text-faint hover:bg-danger-bg hover:text-danger-fg'
          : 'text-faint hover:bg-fill-hover hover:text-strong',
        className
      )}
      onClick={onClick}
      title={label}
      type="button"
    >
      <Icon className="size-3" />
    </button>
  )
}
