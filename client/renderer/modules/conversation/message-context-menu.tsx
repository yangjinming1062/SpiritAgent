import { useContext, useEffect, useLayoutEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

import { useDismissOnOutside } from '@/shared/hooks/use-dismiss-on-outside'
import { Check, type IconComponent } from '@/shared/lib/icons'
import { CaptureWindowIdContext, probeInteractiveRegions, useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { cn } from '@/shared/lib/utils'
import { SURFACE_OVERLAY } from '@/shared/panel/palette'

interface MessageContextMenuItem {
  checked?: boolean
  disabled?: boolean
  icon: IconComponent
  label: string
  onSelect(): void
}

interface MessageContextMenuProps {
  id: string
  items: MessageContextMenuItem[]
  label: string
  onClose(restoreFocus?: boolean): void
  position: { x: number; y: number }
}

export function MessageContextMenu({
  id,
  items,
  label,
  onClose,
  position
}: MessageContextMenuProps): React.JSX.Element {
  const menuRef = useRef<HTMLDivElement>(null)
  const windowId = useContext(CaptureWindowIdContext)
  useInteractiveRegion(id, menuRef)
  useDismissOnOutside(menuRef, true, onClose)

  useLayoutEffect(() => {
    const menu = menuRef.current

    if (!menu) {
      return
    }

    const place = (): void => {
      const bounds = menu.getBoundingClientRect()
      menu.style.left = `${Math.max(8, Math.min(position.x, window.innerWidth - bounds.width - 8))}px`
      menu.style.top = `${Math.max(8, Math.min(position.y, window.innerHeight - bounds.height - 8))}px`
      probeInteractiveRegions(windowId)
    }

    place()
    menu.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus({ preventScroll: true })
    const observer = new ResizeObserver(place)
    observer.observe(menu)

    return () => observer.disconnect()
  }, [position.x, position.y, windowId])

  useEffect(() => {
    const dismiss = (): void => onClose()

    const dismissOnScroll = (event: Event): void => {
      if (event.target instanceof Node && menuRef.current?.contains(event.target)) {
        return
      }

      onClose()
    }

    window.addEventListener('blur', dismiss)
    window.addEventListener('resize', dismiss)
    window.addEventListener('scroll', dismissOnScroll, true)

    return () => {
      window.removeEventListener('blur', dismiss)
      window.removeEventListener('resize', dismiss)
      window.removeEventListener('scroll', dismissOnScroll, true)
    }
  }, [onClose])

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    event.stopPropagation()

    if (event.key === 'Escape' || event.key === 'Tab') {
      if (event.key === 'Escape') {
        event.preventDefault()
      }

      onClose(true)

      return
    }

    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      return
    }

    event.preventDefault()

    const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'))
    const current = buttons.findIndex(button => button === document.activeElement)

    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? buttons.length - 1
          : (current + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length

    buttons[next]?.focus()
  }

  return createPortal(
    <div
      aria-label={label}
      className={cn(
        'fixed z-[100] min-w-44 max-w-[calc(100vw-1rem)] max-h-[calc(100vh-1rem)] overflow-y-auto rounded-xl p-1.5 text-xs text-strong select-none [-webkit-app-region:no-drag]',
        SURFACE_OVERLAY
      )}
      id={id}
      onContextMenu={event => {
        event.preventDefault()
        event.stopPropagation()
      }}
      onKeyDown={handleKeyDown}
      onPointerDown={event => event.stopPropagation()}
      ref={menuRef}
      role="menu"
      style={{ left: position.x, top: position.y }}
    >
      {items.map(({ checked, disabled, icon: Icon, label: itemLabel, onSelect }, index) => (
        <button
          aria-checked={checked}
          className={cn(
            'flex min-h-8 w-full cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-body transition hover:bg-fill-hover hover:text-strong focus:bg-fill-hover focus:text-strong focus:outline-none disabled:cursor-default disabled:opacity-40',
            checked !== undefined && 'mt-1 border-t border-line-hairline'
          )}
          disabled={disabled}
          key={index}
          onClick={event => {
            event.stopPropagation()
            onClose(true)
            onSelect()
          }}
          role={checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
          tabIndex={-1}
          type="button"
        >
          <Icon className="size-4 shrink-0 text-muted" />
          <span className="min-w-0 flex-1">{itemLabel}</span>
          {checked ? <Check aria-hidden="true" className="size-3.5 shrink-0 text-accent" /> : null}
        </button>
      ))}
    </div>,
    document.body
  )
}
