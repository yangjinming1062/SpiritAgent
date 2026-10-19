import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'

import { useEscapeKey } from '../hooks/use-escape-key'
import { ChevronDown } from '../lib/icons'
import { cn } from '../lib/utils'

import { SURFACE_OVERLAY } from './palette'

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
  const [activeIndex, setActiveIndex] = useState(0)
  const [dropUp, setDropUp] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const listId = useId()

  const selected = options.find(o => o.value === value)

  // 打开时把活动项定位到当前选中值，焦点交给列表框承接方向键；贴近窗口下缘时向上展开，避免弹层被窗口底边截断。
  useLayoutEffect(() => {
    if (!open) {
      return
    }

    const root = rootRef.current
    const list = listRef.current

    if (root && list) {
      const rect = root.getBoundingClientRect()
      const spaceBelow = window.innerHeight - rect.bottom
      setDropUp(spaceBelow < list.offsetHeight + 8 && rect.top > spaceBelow)
      list.focus({ preventScroll: true })
    }
  }, [open])

  // 指针悬停与键盘导航共用 activeIndex，滚动时保持活动项可见。
  useEffect(() => {
    if (!open) {
      return
    }

    listRef.current?.querySelector(`[data-index='${activeIndex}']`)?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open])

  const close = useCallback((): void => {
    setOpen(false)
    triggerRef.current?.focus()
  }, [])

  useEscapeKey(close, { enabled: open })

  useEffect(() => {
    if (!open) {
      return
    }

    const onPointerDown = (e: PointerEvent): void => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }

    document.addEventListener('pointerdown', onPointerDown, true)
    window.addEventListener('resize', close)

    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      window.removeEventListener('resize', close)
    }
  }, [close, open])

  const commit = (index: number): void => {
    const option = options[index]

    if (!option) {
      return
    }

    // 与原生 select 一致：值未变化时只收起弹层，不向消费方报告变更。
    if (option.value !== value) {
      onChange(option.value)
    }

    close()
  }

  const openList = (): void => {
    if (options.length === 0) {
      return
    }

    setActiveIndex(
      Math.max(
        0,
        options.findIndex(o => o.value === value)
      )
    )
    setOpen(true)
  }

  const onListKeyDown = (e: React.KeyboardEvent): void => {
    if (e.key === 'Tab') {
      e.preventDefault()
      close()

      return
    }

    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex(i => (i + (e.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length)
    } else if (e.key === 'Home') {
      e.preventDefault()
      setActiveIndex(0)
    } else if (e.key === 'End') {
      e.preventDefault()
      setActiveIndex(options.length - 1)
    } else if (e.key === 'Enter' || e.key === ' ') {
      // 阻止默认：焦点回到触发钮后，按键的 click 激活不能把列表再打开。
      e.preventDefault()
      commit(activeIndex)
    }
  }

  return (
    <div className={cn('relative', widthClass)} ref={rootRef}>
      <button
        aria-controls={open ? listId : undefined}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={ariaLabel}
        className="flex h-8 w-full items-center justify-between gap-2 rounded-lg border border-line-standard bg-fill-faint px-3 text-xs text-strong transition hover:bg-fill-hover disabled:pointer-events-none disabled:opacity-40"
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={e => {
          if (!open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
            e.preventDefault()
            openList()
          }
        }}
        ref={triggerRef}
        type="button"
      >
        <span className="truncate">{selected?.label ?? value}</span>
        <ChevronDown className={cn('size-3.5 shrink-0 text-faint transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div
          aria-activedescendant={`${listId}-opt-${activeIndex}`}
          aria-label={ariaLabel}
          className={cn(
            'absolute right-0 z-50 max-h-64 min-w-full overflow-y-auto rounded-xl p-1',
            SURFACE_OVERLAY,
            dropUp ? 'bottom-full mb-1' : 'mt-1'
          )}
          id={listId}
          onKeyDown={onListKeyDown}
          ref={listRef}
          role="listbox"
          tabIndex={-1}
        >
          {options.map((o, index) => (
            <button
              aria-selected={o.value === value}
              className={cn(
                'flex h-7 w-full items-center rounded-lg px-2.5 text-left text-xs transition',
                o.value === value ? 'bg-accent-soft font-medium text-accent' : 'text-muted',
                index === activeIndex && o.value !== value && 'bg-fill-hover text-strong'
              )}
              data-index={index}
              id={`${listId}-opt-${index}`}
              key={o.value}
              onClick={() => commit(index)}
              onMouseMove={() => setActiveIndex(index)}
              role="option"
              tabIndex={-1}
              type="button"
            >
              <span className="truncate">{o.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
