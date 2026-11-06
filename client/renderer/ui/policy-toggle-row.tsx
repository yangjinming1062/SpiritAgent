import { Toggle } from './components'

/**
 * 政策/偏好开关行：标题 + 说明 + 当前状态文案 + 开关。
 * 状态文案（如「已锁定/允许调整」）由业务给出，保存中由 `status` 呈现。
 */
export function PolicyToggleRow({
  label,
  description,
  status,
  checked,
  disabled = false,
  ariaLabel,
  onChange
}: {
  label: string
  description?: string
  status?: string
  checked: boolean
  disabled?: boolean
  ariaLabel: string
  onChange: () => void
}): React.JSX.Element {
  return (
    <div className="flex items-center justify-between gap-4 p-4">
      <div className="min-w-0">
        <p className="text-sm font-medium text-strong">{label}</p>
        {description ? <p className="mt-1 text-xs leading-relaxed text-muted">{description}</p> : null}
        {status ? (
          <p aria-live="polite" className="mt-1 text-xs text-muted">
            {status}
          </p>
        ) : null}
      </div>
      <Toggle ariaLabel={ariaLabel} checked={checked} disabled={disabled} onChange={onChange} />
    </div>
  )
}
