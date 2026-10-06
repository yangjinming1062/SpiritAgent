import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import {
  BookOpen,
  Globe,
  type IconComponent,
  ImageIcon,
  MessageCircle,
  Messages,
  Monitor,
  Plus,
  Settings,
  Shirt,
  X
} from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { notifyError } from '@/shared/store/notifications'

import { DesktopDockPicker, type DockPickerMode } from './desktop-dock-picker'
import { DESKTOP_APPS, type DesktopApp, type DesktopWindowState } from './desktop-layout'
import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

type DockState = Awaited<ReturnType<typeof window.spiritagent.dock.getState>>
type DockEntry = DockState['entries'][number]

type DockMenuTarget = { kind: 'program'; id: string; pinned: boolean } | { kind: 'internal'; id: DesktopApp }

const APP_ICONS: Record<DesktopApp, IconComponent> = {
  chat: MessageCircle,
  posts: Messages,
  diary: BookOpen,
  scene: ImageIcon,
  appearance: Shirt,
  channels: Globe,
  settings: Settings
}

function BuiltinAppIcon({ id }: { id: DesktopApp }): React.JSX.Element {
  const Icon = APP_ICONS[id]

  return <Icon size={23} />
}

export function DesktopDock({
  windows,
  onActivate,
  onClose,
  menuEnabled,
  onMenuOpenChange
}: {
  windows: DesktopWindowState[]
  onActivate: (id: DesktopApp) => void
  onClose: (id: DesktopApp) => void
  menuEnabled: boolean
  onMenuOpenChange: (open: boolean) => void
}): React.JSX.Element {
  const t = useDesktopStrings()

  const [state, setState] = useState<DockState>({
    entries: [],
    runningEntries: [],
    revision: -1,
    pinnedRevision: -1,
    runningStatus: 'loading',
    runningError: null
  })

  const [menu, setMenu] = useState<(DockMenuTarget & { x: number }) | null>(null)
  const [picker, setPicker] = useState<DockPickerMode | null>(null)
  const dockRef = useRef<HTMLElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  useInteractiveRegion('desktop-dock', dockRef)
  useInteractiveRegion('desktop-dock-menu', menuRef)
  const triggerRef = useRef<HTMLElement | null>(null)
  const [draggingOver, setDraggingOver] = useState(false)
  const draggedItem = useRef<{ id: string; pinned: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const beginAsync = useAsyncGuard()

  const closePicker = useCallback((): void => {
    setPicker(null)
    triggerRef.current?.focus()
  }, [])

  const openPicker = (mode: DockPickerMode, trigger: HTMLElement): void => {
    setMenu(null)
    triggerRef.current = trigger
    setPicker(mode)
  }

  useEffect(() => {
    if (!menuEnabled) {
      setMenu(null)
      setPicker(null)
    }
  }, [menuEnabled])

  useEffect(() => {
    onMenuOpenChange(menu !== null || picker !== null)

    return () => onMenuOpenChange(false)
  }, [menu, onMenuOpenChange, picker])

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
    window.addEventListener('blur', close)

    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('blur', close)
    }
  }, [menu, menuEnabled])

  // 选择面板自己处理遮罩点击，窗口级监听只收 Escape。
  useEffect(() => {
    if (!picker || !menuEnabled) {
      return
    }

    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        closePicker()
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [closePicker, menuEnabled, picker])

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

  const closeWindows = (entry: DockEntry, windowIds: string[]): void => {
    void run(() => window.spiritagent.dock.closeWindows(entry.id, windowIds), t.closeWindow)
    setMenu(null)
  }

  const openMenu = (target: DockMenuTarget, button: HTMLElement): void => {
    if (!menuEnabled) {
      return
    }

    const dock = dockRef.current?.getBoundingClientRect()

    if (!dock) {
      return
    }

    const rect = button.getBoundingClientRect()
    const halfWidth = Math.min(120, (window.innerWidth - 32) / 2)
    const center = Math.max(halfWidth + 16, Math.min(rect.left + rect.width / 2, window.innerWidth - halfWidth - 16))
    setMenu({ ...target, x: center - dock.left })
  }

  const menuIndex = state.entries.findIndex(entry => entry.id === menu?.id)

  const menuEntry =
    menu?.kind === 'program'
      ? menu.pinned
        ? state.entries[menuIndex]
        : state.runningEntries.find(entry => entry.id === menu.id)
      : undefined

  const menuWindow = menu?.kind === 'internal' ? windows.find(item => item.id === menu.id) : undefined

  useEffect(() => {
    if (menu && !menuEntry && !menuWindow) {
      setMenu(null)
    }
  }, [menu, menuEntry, menuWindow])

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

  const dropAt = (event: React.DragEvent, beforeId?: string): void => {
    const dragged = draggedItem.current

    if (!dragged) {
      return
    }

    event.preventDefault()
    event.stopPropagation()
    draggedItem.current = null
    setDraggingOver(false)

    if (!dragged.pinned) {
      void run(() => window.spiritagent.dock.pin(dragged.id, beforeId))

      return
    }

    if (dragged.id === beforeId) {
      return
    }

    const ids = state.entries.map(entry => entry.id).filter(id => id !== dragged.id)
    const index = beforeId === undefined ? ids.length : ids.indexOf(beforeId)

    if (index >= 0) {
      ids.splice(index, 0, dragged.id)

      if (ids.some((id, position) => id !== state.entries[position]?.id)) {
        void run(() => window.spiritagent.dock.reorder(ids))
      }
    }
  }

  const renderProgram = (entry: DockEntry, pinned: boolean): React.JSX.Element => {
    const label = `${entry.name}${entry.running ? ` · ${t.running} (${entry.windows.length})` : ''}`

    return (
      <div className={styles.dockItem} data-entry-id={entry.id} key={entry.id}>
        <button
          aria-label={label}
          className={styles.appIcon}
          data-unavailable={entry.status !== 'ready' && !entry.running}
          disabled={busy}
          draggable={pinned || entry.canPin}
          onClick={() => void run(() => window.spiritagent.dock.activate(entry.id))}
          onContextMenu={event => {
            event.preventDefault()
            openMenu({ kind: 'program', id: entry.id, pinned }, event.currentTarget)
          }}
          onDragEnd={() => {
            draggedItem.current = null
            setDraggingOver(false)
          }}
          onDragStart={event => {
            setMenu(null)
            draggedItem.current = { id: entry.id, pinned }
            event.dataTransfer.effectAllowed = 'move'
            event.dataTransfer.setData('text/plain', entry.id)
          }}
          onKeyDown={event => {
            if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
              event.preventDefault()
              openMenu({ kind: 'program', id: entry.id, pinned }, event.currentTarget)
            }
          }}
          title={
            entry.status === 'loading'
              ? `${entry.name} · ${t.pickLoading}`
              : entry.status === 'ready' || entry.running
                ? label
                : `${entry.name} · ${entry.error || t.missing}`
          }
          type="button"
        >
          {entry.icon ? <img alt="" draggable={false} src={entry.icon} /> : <Monitor size={26} />}
          {entry.running && <i aria-hidden="true" className={styles.runningDot} />}
        </button>
      </div>
    )
  }

  return (
    <>
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
            <div
              aria-label={t.pinnedApps}
              className={styles.dockGroup}
              onDrop={event => {
                const before = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[data-entry-id]')).find(
                  element => {
                    const rect = element.getBoundingClientRect()

                    return event.clientX < rect.left + rect.width / 2
                  }
                )

                dropAt(event, before?.dataset.entryId)
              }}
              role="group"
            >
              {state.entries.map(entry => renderProgram(entry, true))}
              <button
                aria-label={t.addApp}
                className={styles.addApp}
                disabled={busy}
                onClick={event => openPicker({ kind: 'add' }, event.currentTarget)}
                title={t.addApp}
                type="button"
              >
                <Plus size={24} />
              </button>
            </div>
            {(windows.length > 0 || state.runningEntries.length > 0) && <span className={styles.dockDivider} />}
            <div aria-label={t.runningApps} className={styles.dockGroup} role="group">
              {windows
                .toSorted((a, b) => DESKTOP_APPS.indexOf(a.id) - DESKTOP_APPS.indexOf(b.id))
                .map(item => {
                  const label = `${t[item.id]} · ${t.running}${item.minimized ? ` · ${t.minimizedWindow}` : ''}`

                  return (
                    <button
                      aria-label={label}
                      className={styles.internalDockItem}
                      data-minimized={item.minimized}
                      key={item.id}
                      onClick={() => onActivate(item.id)}
                      onContextMenu={event => {
                        event.preventDefault()
                        openMenu({ kind: 'internal', id: item.id }, event.currentTarget)
                      }}
                      onKeyDown={event => {
                        if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
                          event.preventDefault()
                          openMenu({ kind: 'internal', id: item.id }, event.currentTarget)
                        }
                      }}
                      title={label}
                      type="button"
                    >
                      <BuiltinAppIcon id={item.id} />
                      <i aria-hidden="true" className={styles.runningDot} />
                    </button>
                  )
                })}
              {state.runningEntries.map(entry => renderProgram(entry, false))}
            </div>
          </div>
        </div>
        {state.runningStatus === 'unavailable' && (
          <span
            aria-label={t.runningUnavailable}
            className={styles.dockWarning}
            role="status"
            title={`${t.runningUnavailable}${state.runningError ? ` · ${state.runningError}` : ''}`}
          >
            !
          </span>
        )}
        {menuEnabled && menu && (menuEntry || menuWindow) && (
          <div
            className={styles.dockMenu}
            onPointerDown={event => event.stopPropagation()}
            ref={menuRef}
            role="menu"
            style={{ left: menu.x }}
          >
            {menuWindow && (
              <button
                onClick={() => {
                  onClose(menuWindow.id)
                  setMenu(null)
                }}
                role="menuitem"
                type="button"
              >
                {t.closeWindow}
              </button>
            )}
            {menuEntry && menu.kind === 'program' && (
              <>
                {menuEntry.windows.map(runningWindow => (
                  <div className={styles.dockWindowRow} key={runningWindow.id}>
                    <button
                      className={styles.dockWindowActivate}
                      disabled={busy}
                      onClick={() => {
                        void run(() => window.spiritagent.dock.activate(menuEntry.id, runningWindow.id))
                        setMenu(null)
                      }}
                      role="menuitem"
                      title={runningWindow.title}
                      type="button"
                    >
                      <span className={styles.dockWindowTitle}>{runningWindow.title}</span>
                      {runningWindow.minimized && <span className={styles.dockWindowState}>{t.minimizedWindow}</span>}
                    </button>
                    {menuEntry.windows.length > 1 && (
                      <button
                        aria-label={`${t.closeWindow} · ${runningWindow.title}`}
                        className={styles.dockWindowClose}
                        disabled={busy}
                        onClick={() => closeWindows(menuEntry, [runningWindow.id])}
                        role="menuitem"
                        title={t.closeWindow}
                        type="button"
                      >
                        <X size={15} />
                      </button>
                    )}
                  </div>
                ))}
                {menuEntry.windows.length > 0 && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      closeWindows(
                        menuEntry,
                        menuEntry.windows.map(item => item.id)
                      )
                    }
                    role="menuitem"
                    type="button"
                  >
                    {menuEntry.windows.length > 1 ? t.closeAllWindows : t.closeWindow}
                  </button>
                )}
                {menu.pinned && (menuEntry.status === 'missing' || menuEntry.status === 'invalid') && (
                  <button
                    disabled={busy}
                    onClick={event => openPicker({ entryId: menuEntry.id, kind: 'repair' }, event.currentTarget)}
                    role="menuitem"
                    type="button"
                  >
                    {t.repairApp}
                  </button>
                )}
                {menu.pinned ? (
                  <>
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
                      {t.unpinApp}
                    </button>
                  </>
                ) : (
                  <button
                    disabled={busy || !menuEntry.canPin}
                    onClick={() => {
                      void run(() => window.spiritagent.dock.pin(menuEntry.id))
                      setMenu(null)
                    }}
                    role="menuitem"
                    title={menuEntry.canPin ? t.pinApp : t.cannotPin}
                    type="button"
                  >
                    {t.pinApp}
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </nav>
      {picker && (
        <DesktopDockPicker
          dockRevision={state.pinnedRevision}
          mode={picker}
          onClose={closePicker}
          onCommitted={(next, close) => {
            setState(previous => (next.revision >= previous.revision ? next : previous))

            if (close) {
              closePicker()
            }
          }}
        />
      )}
    </>
  )
}
