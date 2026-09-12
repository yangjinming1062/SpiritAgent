import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import type { IconComponent } from '@/shared/lib/icons'
import { ChevronDown, Loader2, Search, X } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import { useEscapeKey } from '../hooks/use-escape-key'

import {
  BTN_DANGER,
  BTN_ICON,
  BTN_PRIMARY,
  BTN_SUBTLE,
  INPUT_CLASS,
  SETTINGS_INTRO_HINT,
  SETTINGS_INTRO_TITLE,
  SETTINGS_ROW_DESC,
  SETTINGS_ROW_TITLE
} from './palette'

// 拖拽柄事件组的透传形状（usePanelDrag 的 bind）——shared 侧不依赖 companion hooks，
// 只要求它是可展开到 DOM 上的对象。
type DragBindProps = object

export function SettingsSectionIntro({ title, hint }: { title: string; hint?: string }): React.JSX.Element {
  return (
    <div className="mb-4">
      <h2 className={SETTINGS_INTRO_TITLE}>{title}</h2>
      {hint ? <p className={SETTINGS_INTRO_HINT}>{hint}</p> : null}
    </div>
  )
}

export function CapsuleTabs<T extends string>({
  ariaLabel,
  onChange,
  options,
  size = 'md',
  value,
  disabled = false
}: {
  ariaLabel?: string
  onChange: (next: T) => void
  options: ReadonlyArray<{ value: T; label: string; icon?: IconComponent }>
  size?: 'md' | 'sm'
  value: T
  disabled?: boolean
}): React.JSX.Element {
  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    if (disabled || (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft')) {
      return
    }

    e.preventDefault()
    const currentIndex = options.findIndex(option => option.value === value)

    if (currentIndex === -1) {
      return
    }

    const nextIndex =
      e.key === 'ArrowRight'
        ? (currentIndex + 1) % options.length
        : (currentIndex - 1 + options.length) % options.length

    onChange(options[nextIndex].value)
  }

  return (
    <div
      aria-label={ariaLabel}
      className={cn(
        'liquid-glass-pill inline-flex max-w-full items-center gap-1 overflow-x-auto rounded-full p-1',
        disabled && 'pointer-events-none opacity-40'
      )}
      onKeyDown={handleKeyDown}
      role="tablist"
    >
      {options.map(option => {
        const active = option.value === value
        const Icon = option.icon

        return (
          <button
            aria-selected={active}
            className={cn(
              'group inline-flex shrink-0 items-center justify-center gap-1.5 rounded-full transition-all duration-200 select-none',
              size === 'sm' ? 'px-3 py-1 text-[12px]' : 'px-3.5 py-1.5 text-[13px]',
              active ? 'liquid-glass-tab-active font-medium text-strong' : 'font-normal text-muted hover:text-strong',
              disabled && 'cursor-not-allowed opacity-40'
            )}
            disabled={disabled}
            key={option.value}
            onClick={() => !disabled && onChange(option.value)}
            role="tab"
            tabIndex={active ? 0 : -1}
            type="button"
          >
            {Icon && (
              <Icon
                className={cn(
                  'shrink-0 transition-colors',
                  size === 'sm' ? 'size-3.5' : 'size-4',
                  active
                    ? 'text-accent drop-shadow-[0_0_6px_var(--ui-accent-soft)]'
                    : 'text-muted group-hover:text-strong'
                )}
              />
            )}
            <span>{option.label}</span>
          </button>
        )
      })}
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

export function Segmented<T extends string>({
  options,
  value,
  onChange
}: {
  options: ReadonlyArray<{ value: T; label: string }>
  value: T
  onChange: (next: T) => void
}): React.JSX.Element {
  return (
    <div className="liquid-glass-pill flex gap-0.5 rounded-full p-0.5" role="tablist">
      {options.map(o => (
        <button
          aria-selected={value === o.value}
          className={cn(
            'flex-1 rounded-full px-3 py-1.5 text-[12px] whitespace-nowrap transition',
            value === o.value ? 'liquid-glass-tab-active font-medium text-strong' : 'text-muted hover:text-strong'
          )}
          key={o.value}
          onClick={() => onChange(o.value)}
          role="tab"
          type="button"
        >
          {o.label}
        </button>
      ))}
    </div>
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
  icon: Icon,
  onClose,
  dragBind,
  dragRegion = false,
  closeLabel = '关闭'
}: {
  title: ReactNode
  icon?: IconComponent
  onClose: () => void
  // 伙伴窗浮层把拖拽柄事件铺在头部；工具窗不传。
  dragBind?: DragBindProps
  // OS 窗口壳（工具窗）没有指针拖拽柄，头部整体标记为系统拖拽区。
  dragRegion?: boolean
  closeLabel?: string
}): React.JSX.Element {
  return (
    <div
      className={cn(
        'relative flex items-center justify-between gap-2 border-b border-line-standard px-4 py-2.5 bg-fill-faint',
        dragBind && 'cursor-grab active:cursor-grabbing',
        dragRegion && '[-webkit-app-region:drag]'
      )}
      title={dragBind ? '拖动以移动面板' : undefined}
      {...dragBind}
    >
      <div className="flex items-center gap-2.5">
        {Icon && (
          <div className="flex size-6 items-center justify-center rounded-md border border-line-standard bg-fill-hover">
            <Icon className="size-3.5 text-accent drop-shadow-[0_0_6px_var(--ui-accent)]" />
          </div>
        )}
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold tracking-wide text-strong">{title}</h2>
          <span className="hidden font-mono text-[9px] text-faint uppercase tracking-widest sm:inline-block">
            [SYS.PANEL]
          </span>
        </div>
      </div>
      <button
        aria-label={closeLabel}
        className={cn(
          BTN_ICON,
          'hover:border hover:border-line-strong hover:bg-danger-bg hover:text-danger-fg',
          dragRegion && '[-webkit-app-region:no-drag]'
        )}
        onClick={onClose}
        type="button"
      >
        <X />
      </button>
    </div>
  )
}

export function Spinner({ className }: { className?: string }): React.JSX.Element {
  return <Loader2 className={cn('size-4 animate-spin text-faint', className)} />
}

export function EmptyState({
  title,
  description,
  action
}: {
  title: string
  description?: string
  action?: ReactNode
}): React.JSX.Element {
  return (
    <div className="grid min-h-36 place-items-center px-4 py-8 text-center">
      <div className="max-w-sm">
        <div className="text-xs font-medium text-muted">{title}</div>
        {description && <div className="mt-1 text-[11px] leading-relaxed text-faint">{description}</div>}
        {action && <div className="mt-3 flex justify-center">{action}</div>}
      </div>
    </div>
  )
}

export function PanelSelect<T extends string>({
  value,
  options,
  onChange,
  disabled = false,
  widthClass = 'w-36',
  ariaLabel
}: {
  value: T
  options: ReadonlyArray<{ value: T; label: string }>
  onChange: (next: T) => void
  disabled?: boolean
  widthClass?: string
  ariaLabel?: string
}): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) {
      return
    }

    const onPointerDown = (e: PointerEvent): void => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }

    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setOpen(false)
      }
    }

    document.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('keydown', onKey, true)

    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  const selected = options.find(o => o.value === value)

  return (
    <div className={cn('relative', widthClass)} ref={rootRef}>
      <button
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={ariaLabel}
        className="flex h-8 w-full items-center justify-between gap-2 rounded-lg border border-line-standard bg-fill-faint px-3 text-xs text-strong transition hover:bg-fill-hover disabled:pointer-events-none disabled:opacity-40"
        disabled={disabled}
        onClick={() => setOpen(o => !o)}
        type="button"
      >
        <span className="truncate">{selected?.label ?? value}</span>
        <ChevronDown className={cn('size-3.5 shrink-0 text-faint transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="absolute right-0 z-50 mt-1 min-w-full overflow-hidden rounded-xl border border-line-standard bg-surface-panel p-1 shadow-2xl">
          {options.map(o => (
            <button
              aria-selected={o.value === value}
              className={cn(
                'flex h-7 w-full items-center rounded-lg px-2.5 text-left text-xs transition',
                o.value === value ? 'bg-accent-soft font-medium text-accent' : 'text-muted hover:bg-fill-hover'
              )}
              key={o.value}
              onClick={() => {
                onChange(o.value)
                setOpen(false)
              }}
              role="option"
              type="button"
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function SearchField({
  value,
  onChange,
  placeholder,
  ariaLabel
}: {
  value: string
  onChange: (next: string) => void
  placeholder?: string
  ariaLabel?: string
}): React.JSX.Element {
  return (
    <div className="relative w-full max-w-sm">
      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
      <input
        aria-label={ariaLabel}
        className={cn(INPUT_CLASS, 'py-1.5 pl-8 pr-8')}
        onChange={e => onChange(e.currentTarget.value)}
        placeholder={placeholder}
        type="text"
        value={value}
      />
      {value && (
        <button
          aria-label="清空搜索"
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

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  confirmLabel: string
  cancelLabel?: string
  variant?: 'default' | 'destructive'
  onConfirm: () => void | Promise<void>
}

// 警示性确认的小型弹窗（清空密钥、重置配置）。自包含深色卡，不依赖 Radix。
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel = '取消',
  variant = 'default',
  onConfirm
}: ConfirmDialogProps): React.JSX.Element {
  const [busy, setBusy] = useState(false)

  useEscapeKey(() => onOpenChange(false), { enabled: open, busy })

  if (!open) {
    return <></>
  }

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/40 p-6 backdrop-blur-sm"
      onPointerDown={e => {
        if (!busy && e.target === e.currentTarget) {
          onOpenChange(false)
        }
      }}
    >
      <div className="w-full max-w-md rounded-2xl border border-line-strong bg-surface-panel p-5 text-strong shadow-2xl">
        <h3 className="text-sm font-semibold">{title}</h3>
        {description && <p className="mt-2 text-xs leading-relaxed text-muted">{description}</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button className={BTN_SUBTLE} disabled={busy} onClick={() => onOpenChange(false)} type="button">
            {cancelLabel}
          </button>
          <button
            className={variant === 'destructive' ? BTN_DANGER : BTN_PRIMARY}
            disabled={busy}
            onClick={() => {
              setBusy(true)

              void Promise.resolve(onConfirm())
                .then(() => onOpenChange(false))
                .catch(() => {
                  // 出错保留弹窗供重试；错误提示由调用方 notify 负责。
                })
                .finally(() => setBusy(false))
            }}
            type="button"
          >
            {busy ? '处理中…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
