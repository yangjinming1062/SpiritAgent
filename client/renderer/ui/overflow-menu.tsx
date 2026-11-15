import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { useEscapeKey } from '@/shared/hooks/use-escape-key'
import { Dots } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'

import { BTN_ICON, SURFACE_OVERLAY } from './palette'

/**
 * 溢出菜单：命令栏与行卡的次级命令收进右对齐面板，点外或 Esc 关闭。
 * 面板内动作由调用方组合，公共件只负责开合与可访问性。
 */
export function OverflowMenu({
  label,
  children,
  className,
  panelClassName
}: {
  label: string
  children: ReactNode
  className?: string
  panelClassName?: string
}): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  const close = (): void => {
    setOpen(false)
    triggerRef.current?.focus()
  }

  useEscapeKey(close, { enabled: open })

  useEffect(() => {
    if (!open) {
      return
    }

    const onPointerDown = (event: PointerEvent): void => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false)
      }
    }

    document.addEventListener('pointerdown', onPointerDown, true)

    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
    }
  }, [open])

  return (
    <div className={cn('relative', className)} ref={rootRef}>
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={label}
        className={BTN_ICON}
        onClick={() => setOpen(value => !value)}
        ref={triggerRef}
        type="button"
      >
        <Dots aria-hidden="true" />
      </button>
      {open ? (
        <div
          aria-label={label}
          className={cn('absolute right-0 z-50 mt-1 min-w-56 rounded-xl p-1.5', SURFACE_OVERLAY, panelClassName)}
          role="menu"
        >
          {children}
        </div>
      ) : null}
    </div>
  )
}

/** 菜单内单个动作；danger 用于删除等破坏性命令。 */
export function OverflowMenuAction({
  label,
  onClick,
  disabled,
  danger
}: {
  label: string
  onClick: () => void
  disabled?: boolean
  danger?: boolean
}): React.JSX.Element {
  return (
    <button
      className={cn(
        'flex w-full items-center rounded-lg px-2.5 py-1.5 text-left text-xs transition hover:bg-fill-hover disabled:pointer-events-none disabled:opacity-40',
        danger ? 'text-danger-fg' : 'text-body hover:text-strong'
      )}
      disabled={disabled}
      onClick={onClick}
      role="menuitem"
      type="button"
    >
      {label}
    </button>
  )
}
