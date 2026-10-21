import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { AccessibilityPermissionButton } from '@/app/components/accessibility-permission-button'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { LayoutGrid, Monitor, X } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { desktopPlatform, shellApi } from '@/shared/platform/desktop'
import { notifyError } from '@/shared/store/notifications'
import { $presentation } from '@/shared/store/presentation'
import { useStrings } from '@/shared/strings'
import type { DesktopBridge } from '@client-shared/ipc/bridge'

import { DesktopDockPicker, type DockPickerMode } from './desktop-dock-picker'
import styles from './desktop.module.css'

type DockState = Awaited<ReturnType<DesktopBridge['dock']['getState']>>
type DockEntry = DockState['entries'][number]
type DockTask = Awaited<ReturnType<DesktopBridge['dock']['tasks']>>[number]

type DockMenuTarget = { id: string; pinned: boolean }
type DockMenuState = DockMenuTarget & { x: number; bottom: number; focusMenu: boolean }
type DockTaskMenuState = { entryId: string; loading: boolean; tasks: DockTask[] }

export function DesktopDock({
  menuEnabled,
  overlayActive,
  onBeforeOpen,
  onContentWidthChange,
  onMenuOpenChange
}: {
  menuEnabled: boolean
  overlayActive: boolean
  onBeforeOpen: () => void
  onContentWidthChange: (width: number) => void
  onMenuOpenChange: (open: boolean) => void
}): React.JSX.Element {
  const t = useStrings().desktopShell
  const presentation = useStore($presentation)
  const isMac = desktopPlatform() === 'darwin'

  const [state, setState] = useState<DockState>({
    entries: [],
    runningEntries: [],
    revision: -1,
    pinnedRevision: -1,
    runningStatus: 'loading',
    runningError: null
  })

  const [menu, setMenu] = useState<DockMenuState | null>(null)
  const [menuTasks, setMenuTasks] = useState<DockTaskMenuState | null>(null)
  const [picker, setPicker] = useState<DockPickerMode | null>(null)
  const dockRef = useRef<HTMLElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const allAppsRef = useRef<HTMLButtonElement>(null)
  useInteractiveRegion('desktop-dock', dockRef)
  useInteractiveRegion('desktop-dock-menu', menuRef, element => {
    const rect = element.getBoundingClientRect()

    return new DOMRect(rect.left, rect.top, rect.width, rect.height + 12)
  })
  const triggerRef = useRef<HTMLElement | null>(null)
  const pickerEpochRef = useRef(0)
  const taskRequestRef = useRef(0)
  const [draggingOver, setDraggingOver] = useState(false)
  const draggedItem = useRef<{ id: string; pinned: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const busyRef = useRef(false)
  const beginAsync = useAsyncGuard()

  const closePicker = useCallback((restoreFocus = true): void => {
    pickerEpochRef.current += 1
    setPicker(null)

    if (restoreFocus) {
      triggerRef.current?.focus()
    }
  }, [])

  const openPicker = (mode: DockPickerMode, trigger: HTMLElement): void => {
    onBeforeOpen()
    setMenu(null)
    pickerEpochRef.current += 1
    triggerRef.current = trigger
    setPicker(mode)
  }

  useEffect(() => {
    if (!menuEnabled || !overlayActive) {
      setMenu(null)
      closePicker(false)
    }
  }, [closePicker, menuEnabled, overlayActive])

  useEffect(() => {
    onMenuOpenChange(menu !== null || picker !== null)
  }, [menu, onMenuOpenChange, picker])

  useLayoutEffect(() => {
    const content = contentRef.current
    const trigger = allAppsRef.current
    const dock = dockRef.current

    if (!content || !trigger || !dock) {
      return
    }

    const update = (): void => {
      const gap = Number.parseFloat(getComputedStyle(dock).columnGap) || 0
      const warning = dock.querySelector<HTMLElement>('[role="status"], [data-permission-request]')
      onContentWidthChange(
        trigger.getBoundingClientRect().width +
          gap +
          content.getBoundingClientRect().width +
          (warning ? warning.getBoundingClientRect().width + gap : 0)
      )
    }

    const observer = new ResizeObserver(update)
    observer.observe(content)
    observer.observe(trigger)
    update()

    return () => observer.disconnect()
  }, [
    onContentWidthChange,
    state.runningStatus,
    presentation.accessibility.granted,
    presentation.accessibility.requestable
  ])

  useEffect(() => {
    let disposed = false

    const apply = (next: DockState): void => {
      if (!disposed) {
        setState(previous => (next.revision >= previous.revision ? next : previous))
      }
    }

    const off = shellApi().dock.onChanged(apply)
    void shellApi()
      .dock.getState()
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
        triggerRef.current?.focus()
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

  useEffect(() => {
    if (!menu || !menu.focusMenu) {
      return
    }

    const frame = window.requestAnimationFrame(() => {
      menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    })

    return () => window.cancelAnimationFrame(frame)
  }, [menu, menuTasks])

  // 选择面板自己处理遮罩点击，窗口级监听只收 Escape。
  useEffect(() => {
    if (!picker || !menuEnabled) {
      return
    }

    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape' && !event.isComposing) {
        event.preventDefault()
        event.stopPropagation()
        closePicker()
      }
    }

    window.addEventListener('keydown', onKey)

    return () => window.removeEventListener('keydown', onKey)
  }, [closePicker, menuEnabled, picker])

  const run = async (action: () => Promise<DockState | void>, fallback = t.dock): Promise<void> => {
    if (busyRef.current) {
      return
    }

    const isLive = beginAsync()
    busyRef.current = true
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
      busyRef.current = false

      if (isLive()) {
        setBusy(false)
      }
    }
  }

  const activateCatalog = (catalogId: string): void => {
    const epoch = pickerEpochRef.current
    const isLive = beginAsync()

    void run(async () => {
      await shellApi().dock.activateCatalog(catalogId)

      if (isLive() && epoch === pickerEpochRef.current) {
        closePicker(false)
      }
    }, t.appLaunchFailed)
  }

  const closeWindows = (entry: DockEntry, windowIds: string[]): void => {
    void run(() => shellApi().dock.closeWindows(entry.id, windowIds), t.closeWindow)
    setMenu(null)
  }

  const activateTask = (entryId: string, taskId: string): void => {
    setMenu(null)
    triggerRef.current?.focus()
    void run(() => shellApi().dock.activateTask(entryId, taskId), t.taskLaunchFailed)
  }

  const openMenu = (target: DockMenuTarget, button: HTMLElement, focusMenu = false): void => {
    if (!menuEnabled) {
      return
    }

    const dock = dockRef.current?.getBoundingClientRect()

    if (!dock) {
      return
    }

    onBeforeOpen()
    closePicker(false)
    const rect = button.getBoundingClientRect()
    const workArea = isMac ? presentation.workArea : null
    const left = workArea?.x ?? 0
    const right = workArea ? workArea.x + workArea.width : window.innerWidth
    const halfWidth = Math.min(120, (right - left - 32) / 2)
    const center = Math.max(left + halfWidth + 16, Math.min(rect.left + rect.width / 2, right - halfWidth - 16))
    const requestId = taskRequestRef.current + 1
    taskRequestRef.current = requestId
    triggerRef.current = button
    setMenuTasks(isMac ? null : { entryId: target.id, loading: true, tasks: [] })
    setMenu({ ...target, focusMenu, x: center, bottom: window.innerHeight - dock.top + 12 })

    if (isMac) {
      return
    }

    void shellApi()
      .dock.tasks(target.id)
      .then(tasks => {
        if (taskRequestRef.current !== requestId) {
          return
        }

        setMenuTasks(current =>
          current?.entryId === target.id ? { entryId: target.id, loading: false, tasks } : current
        )
      })
      .catch(() => {
        if (taskRequestRef.current === requestId) {
          setMenuTasks(current =>
            current?.entryId === target.id ? { entryId: target.id, loading: false, tasks: [] } : current
          )
        }
      })
  }

  const menuIndex = state.entries.findIndex(entry => entry.id === menu?.id)

  const menuEntry = menu
    ? menu.pinned
      ? state.entries[menuIndex]
      : state.runningEntries.find(entry => entry.id === menu.id)
    : undefined

  useEffect(() => {
    if (menu && !menuEntry) {
      setMenu(null)
    }
  }, [menu, menuEntry])

  useEffect(() => {
    if (!menu) {
      taskRequestRef.current += 1
      setMenuTasks(null)
    }
  }, [menu])

  useEffect(() => {
    setMenu(null)
  }, [presentation.workArea?.x, presentation.workArea?.y, presentation.workArea?.width, presentation.workArea?.height])

  const visibleMenuTasks = menuTasks?.entryId === menu?.id && !menuTasks?.loading ? (menuTasks?.tasks ?? []) : []

  const reorder = (from: string, to: string): void => {
    const ids = state.entries.map(entry => entry.id)
    const fromIndex = ids.indexOf(from)
    const toIndex = ids.indexOf(to)

    if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) {
      return
    }

    ids.splice(fromIndex, 1)
    ids.splice(toIndex, 0, from)
    void run(() => shellApi().dock.reorder(ids))
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
      void run(() => shellApi().dock.pin(dragged.id, beforeId))

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
        void run(() => shellApi().dock.reorder(ids))
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
          draggable={pinned || (presentation.accessibility.granted && entry.canPin)}
          onClick={() => void run(() => shellApi().dock.activate(entry.id))}
          onContextMenu={event => {
            event.preventDefault()
            openMenu({ id: entry.id, pinned }, event.currentTarget)
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
              openMenu({ id: entry.id, pinned }, event.currentTarget, true)
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
            void run(() => shellApi().dock.addDroppedFiles(files))
          }
        }}
        ref={dockRef}
        title={t.dockHint}
      >
        <button
          aria-controls={picker?.kind === 'browse' ? 'desktop-apps-panel' : undefined}
          aria-expanded={picker?.kind === 'browse'}
          aria-haspopup="dialog"
          aria-label={t.allApps}
          className={styles.allAppsButton}
          disabled={!menuEnabled}
          onClick={event => {
            if (picker?.kind === 'browse') {
              closePicker(false)
            } else {
              openPicker({ kind: 'browse' }, event.currentTarget)
            }
          }}
          onDrop={event => dropAt(event)}
          ref={allAppsRef}
          title={t.allApps}
          type="button"
        >
          <LayoutGrid size={25} />
        </button>
        <div className={styles.dockViewport} onScroll={() => setMenu(null)}>
          <div className={styles.dockItems} ref={contentRef}>
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
            </div>
            {state.entries.length > 0 && state.runningEntries.length > 0 && <span className={styles.dockDivider} />}
            <div aria-label={t.runningApps} className={styles.dockGroup} role="group">
              {state.runningEntries.map(entry => renderProgram(entry, false))}
            </div>
          </div>
        </div>
        {state.runningStatus === 'unavailable' &&
        !presentation.accessibility.granted &&
        presentation.accessibility.requestable ? (
          <AccessibilityPermissionButton className={styles.dockPermission} compact />
        ) : (
          state.runningStatus === 'unavailable' && (
            <span
              aria-label={t.runningUnavailable}
              className={styles.dockWarning}
              role="status"
              title={`${t.runningUnavailable}${state.runningError ? ` · ${state.runningError}` : ''}`}
            >
              !
            </span>
          )
        )}
        {menuEnabled && menu && menuEntry && (
          <div
            className={styles.dockMenu}
            onPointerDown={event => event.stopPropagation()}
            ref={menuRef}
            role="menu"
            style={{ left: menu.x, bottom: `max(${menu.bottom}px, var(--desktop-popup-bottom))` }}
          >
            {menuTasks?.entryId === menu.id && menuTasks.loading && (
              <div className={styles.dockMenuHeading} role="presentation">
                {t.tasksLoading}
              </div>
            )}
            {visibleMenuTasks.length > 0 && (
              <>
                <div className={styles.dockMenuHeading} role="presentation">
                  {t.tasks}
                </div>
                {visibleMenuTasks.map(task => (
                  <button
                    disabled={busy}
                    key={task.id}
                    onClick={() => activateTask(menu.id, task.id)}
                    role="menuitem"
                    title={task.description || task.title}
                    type="button"
                  >
                    <span className={styles.dockTaskItem}>
                      {task.icon ? <img alt="" draggable={false} src={task.icon} /> : null}
                      <span>{task.title}</span>
                    </span>
                  </button>
                ))}
                <div className={styles.dockMenuSeparator} role="separator" />
              </>
            )}
            {menuEntry && (
              <>
                {menuEntry.windows.map(runningWindow => (
                  <div className={styles.dockWindowRow} key={runningWindow.id}>
                    <button
                      className={styles.dockWindowActivate}
                      disabled={busy || !presentation.accessibility.granted}
                      onClick={() => {
                        void run(() => shellApi().dock.activate(menuEntry.id, runningWindow.id))
                        setMenu(null)
                      }}
                      role="menuitem"
                      title={presentation.accessibility.granted ? runningWindow.title : t.accessibilityDescription}
                      type="button"
                    >
                      <span className={styles.dockWindowTitle}>{runningWindow.title}</span>
                      {runningWindow.minimized && <span className={styles.dockWindowState}>{t.minimizedWindow}</span>}
                    </button>
                    {menuEntry.windows.length > 1 && (
                      <button
                        aria-label={`${t.closeWindow} · ${runningWindow.title}`}
                        className={styles.dockWindowClose}
                        disabled={busy || !presentation.accessibility.granted}
                        onClick={() => closeWindows(menuEntry, [runningWindow.id])}
                        role="menuitem"
                        title={presentation.accessibility.granted ? t.closeWindow : t.accessibilityDescription}
                        type="button"
                      >
                        <X size={15} />
                      </button>
                    )}
                  </div>
                ))}
                {menuEntry.windows.length > 0 && (
                  <button
                    disabled={busy || !presentation.accessibility.granted}
                    onClick={() =>
                      closeWindows(
                        menuEntry,
                        menuEntry.windows.map(item => item.id)
                      )
                    }
                    role="menuitem"
                    title={presentation.accessibility.granted ? undefined : t.accessibilityDescription}
                    type="button"
                  >
                    {menuEntry.windows.length > 1 ? t.closeAllWindows : t.closeWindow}
                  </button>
                )}
                {menu.pinned && (menuEntry.status === 'missing' || menuEntry.status === 'invalid') && (
                  <button
                    disabled={busy}
                    onClick={event =>
                      openPicker({ entryId: menuEntry.id, kind: 'repair' }, triggerRef.current ?? event.currentTarget)
                    }
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
                        void run(() => shellApi().dock.remove(menuEntry.id))
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
                    disabled={busy || !menuEntry.canPin || !presentation.accessibility.granted}
                    onClick={() => {
                      void run(() => shellApi().dock.pin(menuEntry.id))
                      setMenu(null)
                    }}
                    role="menuitem"
                    title={
                      !presentation.accessibility.granted
                        ? t.accessibilityDescription
                        : menuEntry.canPin
                          ? t.pinApp
                          : t.cannotPin
                    }
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
          activating={busy}
          anchorRef={dockRef}
          dockRevision={state.pinnedRevision}
          key={pickerEpochRef.current}
          mode={picker}
          onActivate={activateCatalog}
          onClose={closePicker}
          onCommitted={(next, close) => {
            setState(previous => (next.revision >= previous.revision ? next : previous))

            if (close) {
              closePicker()
            }
          }}
          triggerRef={triggerRef}
        />
      )}
    </>
  )
}
