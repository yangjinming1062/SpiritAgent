import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { useDismissOnOutside } from '@/shared/hooks/use-dismiss-on-outside'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { useStrings } from '@/shared/strings'

import { type DesktopApp, type DesktopWindowState } from './desktop-layout'
import styles from './desktop.module.css'

const DESKTOP_NAV_APPS = ['companion', 'posts', 'diary', 'visuals', 'settings'] as const satisfies readonly DesktopApp[]

type DesktopNavApp = (typeof DESKTOP_NAV_APPS)[number]

export function DesktopProductNav({
  windows,
  activeWindow,
  postsUnread,
  diaryUnread,
  overlayActive,
  onActivate,
  onClose,
  onBeforeOpen,
  onMenuOpenChange
}: {
  windows: DesktopWindowState[]
  activeWindow?: DesktopApp
  postsUnread: boolean
  diaryUnread: boolean
  overlayActive: boolean
  onActivate: (id: DesktopApp) => void
  onClose: (id: DesktopApp) => void
  onBeforeOpen: () => void
  onMenuOpenChange: (open: boolean) => void
}): React.JSX.Element {
  const dict = useStrings()
  const t = dict.desktopShell
  const [menu, setMenu] = useState<{ id: DesktopNavApp; x: number; bottom: number } | null>(null)
  const [hint, setHint] = useState<{ id: DesktopNavApp; x: number; bottom: number } | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  useInteractiveRegion('desktop-product-menu', menuRef, element => {
    const rect = element.getBoundingClientRect()

    return new DOMRect(rect.left, rect.top, rect.width, rect.height + 12)
  })
  useDismissOnOutside(menuRef, Boolean(menu), () => setMenu(null))

  useEffect(() => {
    const clearHint = (): void => setHint(null)
    window.addEventListener('resize', clearHint)
    window.addEventListener('scroll', clearHint, true)

    return () => {
      window.removeEventListener('resize', clearHint)
      window.removeEventListener('scroll', clearHint, true)
    }
  }, [])

  useEffect(() => {
    onMenuOpenChange(menu !== null)
  }, [menu, onMenuOpenChange])

  useEffect(() => {
    if (!overlayActive || (menu && !windows.some(item => item.id === menu.id))) {
      setMenu(null)
    }
  }, [menu, overlayActive, windows])

  useEffect(() => {
    if (!menu) {
      return
    }

    const close = (): void => setMenu(null)

    menuRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
    window.addEventListener('blur', close)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', close, true)

    return () => {
      window.removeEventListener('blur', close)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [menu])

  const openMenu = (id: DesktopNavApp, trigger: HTMLButtonElement): void => {
    if (!windows.some(item => item.id === id)) {
      return
    }

    const rect = trigger.getBoundingClientRect()
    triggerRef.current = trigger
    setHint(null)
    onBeforeOpen()
    setMenu({
      id,
      x: Math.max(16, Math.min(rect.left + rect.width / 2 - 100, window.innerWidth - 216)),
      bottom: window.innerHeight - rect.top + 12
    })
  }

  const showHint = (id: DesktopNavApp, trigger: HTMLButtonElement): void => {
    const rect = trigger.getBoundingClientRect()
    setHint({
      id,
      x: Math.max(90, Math.min(rect.left + rect.width / 2, window.innerWidth - 90)),
      bottom: window.innerHeight - rect.top + 8
    })
  }

  return (
    <>
      <nav aria-label={t.title} className={styles.productNav}>
        {DESKTOP_NAV_APPS.map(id => {
          const item = windows.find(candidate => candidate.id === id)
          const unread = id === 'posts' ? postsUnread : id === 'diary' ? diaryUnread : false

          const label = [
            t[id],
            item && t.running,
            item?.minimized && t.minimizedWindow,
            unread && dict.living.rail.unread
          ]
            .filter(Boolean)
            .join(' · ')

          return (
            <button
              aria-current={activeWindow === id ? 'page' : undefined}
              aria-expanded={item ? menu?.id === id : undefined}
              aria-haspopup={item ? 'menu' : undefined}
              aria-label={label}
              className={styles.internalDockItem}
              data-current={activeWindow === id}
              data-minimized={item?.minimized ?? false}
              key={id}
              onBlur={() => setHint(null)}
              onClick={() => {
                setHint(null)
                onActivate(id)
              }}
              onContextMenu={event => {
                event.preventDefault()
                openMenu(id, event.currentTarget)
              }}
              onFocus={event => showHint(id, event.currentTarget)}
              onKeyDown={event => {
                if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
                  event.preventDefault()
                  openMenu(id, event.currentTarget)
                }
              }}
              onPointerEnter={event => showHint(id, event.currentTarget)}
              onPointerLeave={() => setHint(null)}
              type="button"
            >
              <img alt="" draggable={false} src={`${import.meta.env.BASE_URL}desktop/${id}.svg`} />
              {item && <i aria-hidden="true" className={styles.runningDot} />}
              {unread && <span aria-hidden="true" className={styles.productUnread} />}
            </button>
          )
        })}
      </nav>
      {hint && !menu && (
        <span className={styles.productTooltip} role="tooltip" style={{ left: hint.x, bottom: hint.bottom }}>
          {t[hint.id]}
        </span>
      )}
      {menu && (
        <div
          aria-label={t[menu.id]}
          className={`${styles.dockMenu} ${styles.productMenu}`}
          onBlur={event => {
            if (!event.currentTarget.contains(event.relatedTarget)) {
              setMenu(null)
            }
          }}
          onKeyDown={event => {
            if (event.key === 'Escape' && !event.nativeEvent.isComposing) {
              event.preventDefault()
              event.stopPropagation()
              setMenu(null)
              triggerRef.current?.focus()
            }
          }}
          ref={menuRef}
          role="menu"
          style={{ left: menu.x, bottom: `max(${menu.bottom}px, var(--desktop-popup-bottom))` }}
        >
          <button
            onClick={() => {
              onClose(menu.id)
              setMenu(null)
              triggerRef.current?.focus()
            }}
            role="menuitem"
            type="button"
          >
            {t.closeWindow}
          </button>
        </div>
      )}
    </>
  )
}
