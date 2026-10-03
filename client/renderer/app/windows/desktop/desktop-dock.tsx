import type React from 'react'
import { useEffect, useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import {
  BookOpen,
  Cpu,
  Globe,
  type IconComponent,
  ImageIcon,
  MessageCircle,
  Messages,
  Monitor,
  Plus,
  Settings,
  Shirt
} from '@/shared/lib/icons'
import { notifyError } from '@/shared/store/notifications'

import { type DesktopApp, type DesktopWindowState } from './desktop-layout'
import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

type DockState = Awaited<ReturnType<typeof window.spiritagent.dock.getState>>

const APP_ICONS: Record<DesktopApp, IconComponent> = {
  chat: MessageCircle,
  posts: Messages,
  diary: BookOpen,
  scene: ImageIcon,
  appearance: Shirt,
  channels: Globe,
  settings: Settings,
  station: Cpu
}

function BuiltinAppIcon({ id }: { id: DesktopApp }): React.JSX.Element {
  const Icon = APP_ICONS[id]

  return <Icon size={23} />
}

export function DesktopDock({
  windows,
  onActivate,
  menuEnabled,
  onMenuOpenChange
}: {
  windows: DesktopWindowState[]
  onActivate: (id: DesktopApp) => void
  menuEnabled: boolean
  onMenuOpenChange: (open: boolean) => void
}): React.JSX.Element {
  const t = useDesktopStrings()
  const [state, setState] = useState<DockState>({ entries: [], revision: -1 })
  const [menu, setMenu] = useState<{ id: string; x: number } | null>(null)
  const dockRef = useRef<HTMLElement>(null)
  const [draggingOver, setDraggingOver] = useState(false)
  const draggedId = useRef<string | null>(null)
  const [busy, setBusy] = useState(false)
  const beginAsync = useAsyncGuard()

  useEffect(() => {
    if (!menuEnabled) {
      setMenu(null)
    }
  }, [menuEnabled])

  useEffect(() => {
    onMenuOpenChange(menu !== null)

    return () => onMenuOpenChange(false)
  }, [menu, onMenuOpenChange])

  useEffect(() => {
    let disposed = false

    const apply = (next: DockState): void => {
      if (!disposed) {
        setState(previous => (next.revision >= previous.revision ? next : previous))
      }
    }

    const off = window.spiritagent.dock.onChanged(apply)
    void window.spiritagent.dock
      .getState()
      .then(apply)
      .catch(error => {
        if (!disposed) {
          notifyError(error, t.dock)
        }
      })

    return () => {
      disposed = true
      off()
    }
  }, [t.dock])

  useEffect(() => {
    if (!menu || !menuEnabled) {
      return
    }

    const close = (): void => setMenu(null)

    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close()
      }
    }

    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', onKey)

    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [menu, menuEnabled])

  const run = async (action: () => Promise<DockState | void>, fallback = t.dock): Promise<void> => {
    if (busy) {
      return
    }

    const isLive = beginAsync()
    setBusy(true)

    try {
      const next = await action()

      if (next && isLive()) {
        setState(previous => (next.revision >= previous.revision ? next : previous))
      }
    } catch (error) {
      if (isLive()) {
        notifyError(error, fallback)
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  const openMenu = (id: string, button: HTMLElement): void => {
    if (!menuEnabled) {
      return
    }

    const dock = dockRef.current?.getBoundingClientRect()

    if (!dock) {
      return
    }

    const rect = button.getBoundingClientRect()
    setMenu({ id, x: Math.max(80, Math.min(rect.left + rect.width / 2 - dock.left, dock.width - 80)) })
  }

  const menuIndex = state.entries.findIndex(entry => entry.id === menu?.id)
  const menuEntry = state.entries[menuIndex]

  const reorder = (from: string, to: string): void => {
    const ids = state.entries.map(entry => entry.id)
    const fromIndex = ids.indexOf(from)
    const toIndex = ids.indexOf(to)

    if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) {
      return
    }

    ids.splice(fromIndex, 1)
    ids.splice(toIndex, 0, from)
    void run(() => window.spiritagent.dock.reorder(ids))
  }

  return (
    <nav
      aria-label={t.dock}
      className={styles.dock}
      data-drop={draggingOver}
      onDragLeave={event => {
        if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
          setDraggingOver(false)
        }
      }}
      onDragOver={event => {
        event.preventDefault()
        setDraggingOver(true)
      }}
      onDrop={event => {
        event.preventDefault()
        setDraggingOver(false)
        const files = Array.from(event.dataTransfer.files)

        if (files.length) {
          void run(() => window.spiritagent.dock.addDroppedFiles(files))
        }
      }}
      ref={dockRef}
      title={t.dockHint}
    >
      <div className={styles.dockViewport} onScroll={() => setMenu(null)}>
        <div className={styles.dockItems}>
          {windows.map(item => (
            <button
              aria-label={t[item.id]}
              className={styles.internalDockItem}
              data-minimized={item.minimized}
              key={item.id}
              onClick={() => onActivate(item.id)}
              title={t[item.id]}
              type="button"
            >
              <BuiltinAppIcon id={item.id} />
              <i />
            </button>
          ))}
          {windows.length > 0 && <span className={styles.dockDivider} />}
          {state.entries.map(entry => (
            <div className={styles.dockItem} key={entry.id}>
              <button
                aria-label={entry.name}
                className={styles.appIcon}
                data-unavailable={entry.status !== 'ready'}
                disabled={busy}
                draggable
                onClick={() => void run(() => window.spiritagent.dock.launch(entry.id))}
                onContextMenu={event => {
                  event.preventDefault()
                  openMenu(entry.id, event.currentTarget)
                }}
                onDragEnd={() => {
                  draggedId.current = null
                  setDraggingOver(false)
                }}
                onDragStart={event => {
                  draggedId.current = entry.id
                  event.dataTransfer.effectAllowed = 'move'
                  event.dataTransfer.setData('text/plain', entry.id)
                }}
                onDrop={event => {
                  if (draggedId.current) {
                    event.preventDefault()
                    event.stopPropagation()
                    setDraggingOver(false)
                    reorder(draggedId.current, entry.id)
                    draggedId.current = null
                  }
                }}
                onKeyDown={event => {
                  if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
                    event.preventDefault()
                    openMenu(entry.id, event.currentTarget)
                  }
                }}
                title={entry.status === 'ready' ? entry.name : `${entry.name} · ${entry.error || t.missing}`}
                type="button"
              >
                {entry.icon ? <img alt="" draggable={false} src={entry.icon} /> : <Monitor size={26} />}
              </button>
            </div>
          ))}
        </div>
      </div>
      {menuEnabled && menu && menuEntry && (
        <div
          className={styles.dockMenu}
          onPointerDown={event => event.stopPropagation()}
          role="menu"
          style={{ left: menu.x }}
        >
          {menuEntry.status !== 'ready' && (
            <button
              disabled={busy}
              onClick={() => {
                void run(() => window.spiritagent.dock.repair(menuEntry.id), t.repairApp)
                setMenu(null)
              }}
              role="menuitem"
              type="button"
            >
              {t.repairApp}
            </button>
          )}
          <button
            disabled={menuIndex === 0 || busy}
            onClick={() => {
              const previous = state.entries[menuIndex - 1]

              if (previous) {
                reorder(menuEntry.id, previous.id)
              }

              setMenu(null)
            }}
            role="menuitem"
            type="button"
          >
            {t.moveLeft}
          </button>
          <button
            disabled={menuIndex === state.entries.length - 1 || busy}
            onClick={() => {
              const next = state.entries[menuIndex + 1]

              if (next) {
                reorder(menuEntry.id, next.id)
              }

              setMenu(null)
            }}
            role="menuitem"
            type="button"
          >
            {t.moveRight}
          </button>
          <button
            disabled={busy}
            onClick={() => {
              void run(() => window.spiritagent.dock.remove(menuEntry.id))
              setMenu(null)
            }}
            role="menuitem"
            type="button"
          >
            {t.removeApp}
          </button>
        </div>
      )}
      <button
        aria-label={t.addApp}
        className={styles.addApp}
        disabled={busy}
        onClick={() => void run(() => window.spiritagent.dock.addFromPicker())}
        title={t.addApp}
        type="button"
      >
        <Plus size={24} />
      </button>
    </nav>
  )
}
