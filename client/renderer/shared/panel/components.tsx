import type { ReactNode, Ref } from 'react'

import { type IconComponent, Loader2, Search, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { useStrings } from '@/shared/strings'

import {
  BTN_ICON,
  INPUT_CLASS,
  SETTINGS_INTRO_HINT,
  SETTINGS_INTRO_TITLE,
  SETTINGS_ROW_DESC,
  SETTINGS_ROW_TITLE
} from './palette'

export function SettingsSectionIntro({ title, hint }: { title: string; hint?: string }): React.JSX.Element {
  return (
    <div className="mb-4">
      <h2 className={SETTINGS_INTRO_TITLE}>{title}</h2>
      {hint ? <p className={SETTINGS_INTRO_HINT}>{hint}</p> : null}
    </div>
  )
}

// 分组胶囊：多行 SettingRow 之间用 hairline 分隔。清透档走液态玻璃，不铺乳白实底。
export function SettingCard({
  ariaLabel,
  children,
  className,
  divided = true,
  role
}: {
  ariaLabel?: string
  children: ReactNode
  className?: string
  divided?: boolean
  role?: string
}): React.JSX.Element {
  return (
    <div
      aria-label={ariaLabel}
      className={cn(
        'liquid-glass-card overflow-hidden rounded-2xl',
        divided && 'divide-y divide-line-hairline',
        className
      )}
      role={role}
    >
      {children}
    </div>
  )
}

export function SettingRow({
  label,
  description,
  children,
  stacked = false
}: {
  label: ReactNode
  description?: ReactNode
  children: ReactNode
  // 控件较宽（分段控件 / 档位卡）时改为上下堆叠
  stacked?: boolean
}): React.JSX.Element {
  return (
    <div
      className={cn(
        'grid gap-2 px-4 py-3',
        stacked ? 'grid-cols-1' : 'sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center'
      )}
    >
      <div className="min-w-0">
        <div className={SETTINGS_ROW_TITLE}>{label}</div>
        {description && <div className={SETTINGS_ROW_DESC}>{description}</div>}
      </div>
      <div className={cn('min-w-0', !stacked && 'sm:justify-self-end')}>{children}</div>
    </div>
  )
}

export function Toggle({
  checked,
  onChange,
  disabled = false,
  ariaLabel
}: {
  checked: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
  ariaLabel?: string
}): React.JSX.Element {
  return (
    <button
      aria-checked={checked}
      aria-label={ariaLabel}
      className={cn(
        'relative h-5 w-9 shrink-0 rounded-full border transition-colors disabled:pointer-events-none disabled:opacity-40',
        checked ? 'border-transparent bg-accent' : 'border-line-strong bg-fill-faint'
      )}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      role="switch"
      type="button"
    >
      <span
        className={cn(
          'absolute top-1/2 size-4 -translate-y-1/2 rounded-full transition-[left,background-color] duration-150',
          checked ? 'left-[calc(100%-1.125rem)] bg-on-accent' : 'left-0.5 bg-text-muted'
        )}
      />
    </button>
  )
}

// 数值滑杆：品牌蓝已填充段（--sa-slider-fill 由内联注入）+ 圆钮，皮肤在 styles.css 的 .sa-slider。
export function Slider({
  value,
  min,
  max,
  step = 0.05,
  disabled = false,
  ariaLabel,
  onChange
}: {
  value: number
  min: number
  max: number
  step?: number
  disabled?: boolean
  ariaLabel?: string
  onChange: (next: number) => void
}): React.JSX.Element {
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0

  return (
    <input
      aria-label={ariaLabel}
      className="sa-slider w-full"
      disabled={disabled}
      max={max}
      min={min}
      onChange={e => onChange(Number(e.currentTarget.value))}
      step={step}
      style={{ '--sa-slider-fill': `${pct}%` } as React.CSSProperties}
      type="range"
      value={value}
    />
  )
}

export function PanelHeader({
  title,
  onClose,
  closeDisabled = false,
  onPointerDown,
  draggable = false
}: {
  title: ReactNode
  onClose: () => void
  closeDisabled?: boolean
  onPointerDown?: React.PointerEventHandler<HTMLDivElement>
  draggable?: boolean
}): React.JSX.Element {
  const t = useStrings()

  return (
    <div
      className={cn(
        'relative flex items-center justify-between gap-2 border-b border-line-standard bg-fill-faint px-4 py-2.5',
        draggable && 'cursor-move select-none touch-none'
      )}
      onPointerDown={onPointerDown}
    >
      <h2 className="text-sm font-semibold tracking-wide text-strong">{title}</h2>
      <button
        aria-label={t.common.close}
        className={cn(BTN_ICON, 'hover:border hover:border-line-strong hover:bg-danger-bg hover:text-danger-fg')}
        disabled={closeDisabled}
        onClick={onClose}
        onPointerDown={event => event.stopPropagation()}
        type="button"
      >
        <X />
      </button>
    </div>
  )
}

export function Spinner({
  ariaLabel,
  className,
  role,
  size = 'md',
  tone = 'faint'
}: {
  /** 读屏下的状态名称。 */
  ariaLabel?: string
  className?: string
  role?: string
  size?: 'sm' | 'md' | 'lg'
  tone?: 'faint' | 'muted' | 'accent'
}): React.JSX.Element {
  return (
    <Loader2
      aria-label={ariaLabel}
      className={cn(
        'motion-safe:animate-spin',
        size === 'sm' ? 'size-3.5' : size === 'lg' ? 'size-5' : 'size-4',
        tone === 'accent' ? 'text-accent' : tone === 'muted' ? 'text-muted' : 'text-faint',
        className
      )}
      role={role}
    />
  )
}

export function EmptyState({
  title,
  description,
  action,
  icon: Icon,
  iconTone = 'accent'
}: {
  title: string
  description?: string
  action?: ReactNode
  icon?: IconComponent
  iconTone?: 'accent' | 'muted'
}): React.JSX.Element {
  return (
    <div className="grid min-h-36 place-items-center px-4 py-8 text-center">
      <div className="max-w-sm">
        {Icon && (
          <Icon
            aria-hidden="true"
            className={cn('mx-auto mb-2 size-6', iconTone === 'accent' ? 'text-accent' : 'text-muted')}
          />
        )}
        <div className="text-xs font-medium text-muted">{title}</div>
        {description && <div className="mt-1 text-[11px] leading-relaxed text-faint">{description}</div>}
        {action && <div className="mt-3 flex justify-center">{action}</div>}
      </div>
    </div>
  )
}

export function SearchField({
  value,
  onChange,
  placeholder,
  ariaLabel,
  className,
  inputRef,
  onKeyDown,
  type = 'text'
}: {
  value: string
  onChange: (next: string) => void
  placeholder?: string
  /** 搜索框的无障碍名称。必填，避免出现没有名称的搜索输入。 */
  ariaLabel: string
  className?: string
  /** 需要聚焦或读取输入框时传入。 */
  inputRef?: Ref<HTMLInputElement>
  onKeyDown?: React.KeyboardEventHandler<HTMLInputElement>
  type?: 'text' | 'search'
}): React.JSX.Element {
  const t = useStrings()

  return (
    <div className={cn('relative w-full max-w-sm', className)}>
      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
      <input
        aria-label={ariaLabel}
        className={cn(INPUT_CLASS, 'py-1.5 pl-8 pr-8')}
        onChange={e => onChange(e.currentTarget.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        ref={inputRef}
        type={type}
        value={value}
      />
      {value && (
        <button
          aria-label={t.ui.search.clear}
          className="absolute right-1.5 top-1/2 flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-faint transition hover:bg-fill-hover hover:text-strong"
          onClick={() => onChange('')}
          type="button"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}

export function LoadingBlock({ label }: { label?: string }): React.JSX.Element {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-xs text-faint">
      <Spinner />
      {label}
    </div>
  )
}
