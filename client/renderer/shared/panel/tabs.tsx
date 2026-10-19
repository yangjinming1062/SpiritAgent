import type React from 'react'

import type { IconComponent } from '../lib/icons'
import { cn } from '../lib/utils'

export function CapsuleTabs<T extends string>({
  ariaLabel,
  disabled = false,
  onChange,
  options,
  size = 'md',
  value,
  variant = 'capsule'
}: {
  ariaLabel?: string
  disabled?: boolean
  onChange: (next: T) => void
  options: ReadonlyArray<{ value: T; label: string; icon?: IconComponent }>
  size?: 'md' | 'sm'
  value: T
  variant?: 'capsule' | 'segmented'
}): React.JSX.Element {
  const segmented = variant === 'segmented'

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    if (disabled || options.length === 0) {
      return
    }

    const currentIndex = options.findIndex(option => option.value === value)

    if (currentIndex < 0) {
      return
    }

    let nextIndex: number

    switch (event.key) {
      case 'ArrowRight':
        nextIndex = (currentIndex + 1) % options.length

        break

      case 'ArrowLeft':
        nextIndex = (currentIndex - 1 + options.length) % options.length

        break

      case 'Home':
        nextIndex = 0

        break

      case 'End':
        nextIndex = options.length - 1

        break

      default:
        return
    }

    event.preventDefault()
    onChange(options[nextIndex].value)
    event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIndex]?.focus()
  }

  return (
    <div
      aria-label={ariaLabel}
      className={cn(
        'liquid-glass-pill rounded-full',
        segmented ? 'flex gap-0.5 p-0.5' : 'inline-flex max-w-full items-center gap-1 overflow-x-auto p-1',
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
              'sa-panel-tab group rounded-full transition select-none',
              segmented
                ? 'flex-1 px-3 py-1.5 text-[12px] whitespace-nowrap'
                : cn(
                    'inline-flex shrink-0 items-center justify-center gap-1.5 transition-all duration-200',
                    size === 'sm' ? 'px-3 py-1 text-[12px]' : 'px-3.5 py-1.5 text-[13px]'
                  ),
              active ? 'liquid-glass-tab-active font-medium text-strong' : 'font-normal text-muted hover:text-strong',
              disabled && 'cursor-not-allowed opacity-40'
            )}
            disabled={disabled}
            key={option.value}
            onClick={() => onChange(option.value)}
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
