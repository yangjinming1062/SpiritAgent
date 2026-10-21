import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { Check, FolderOpen, Monitor, Pin, RefreshCw, Search, X } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { shellApi } from '@/shared/platform/desktop'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { DesktopBridge } from '@client-shared/ipc/bridge'

import styles from './desktop.module.css'

type Catalog = Awaited<ReturnType<DesktopBridge['dock']['catalog']>>
type CatalogItem = Catalog['items'][number]
type DockState = Awaited<ReturnType<DesktopBridge['dock']['getState']>>

const ICON_BATCH = 60
const ICON_DEBOUNCE_MS = 120

export type DockPickerMode = { kind: 'browse' } | { kind: 'repair'; entryId: string }

function matches(item: CatalogItem, query: string): boolean {
  return item.name.toLowerCase().includes(query) || item.detail.toLowerCase().includes(query)
}

export function DesktopDockPicker({
  activating,
  anchorRef,
  dockRevision,
  mode,
  onActivate,
  onClose,
  onCommitted,
  triggerRef
}: {
  activating: boolean
  /** 观察 Dock 尺寸，固定程序改变底栏宽度时同步入口位置。 */
  anchorRef: React.RefObject<HTMLElement | null>
  /** 固定配置版本：窗口与图标更新不触发目录查询。 */
  dockRevision: number
  mode: DockPickerMode
  onActivate: (catalogId: string) => void
  onClose: (restoreFocus?: boolean) => void
  /** 目录修复成功后关闭；固定和文件对话框取消时保留面板。 */
  onCommitted: (state: DockState, close: boolean) => void
  triggerRef: React.RefObject<HTMLElement | null>
}): React.JSX.Element {
  const t = useStrings().desktopShell
  const locale = useStore($locale)
  const beginAsync = useAsyncGuard()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [failed, setFailed] = useState(false)
  const [query, setQuery] = useState('')
  const [icons, setIcons] = useState<Record<string, string | null>>({})
  const [viewportIds, setViewportIds] = useState<string[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [position, setPosition] = useState(() => ({
    bottom: 98,
    left: window.innerWidth / 2,
    maxHeight: Math.max(0, Math.min(620, window.innerHeight - 150))
  }))

  const listRef = useRef<HTMLUListElement>(null)
  const pickerRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const restoreSearchFocusRef = useRef(false)
  const browsingRef = useRef(false)
  const busyRef = useRef(false)
  const catalogRequestRef = useRef(0)
  const catalogRevisionRef = useRef<number | undefined>(undefined)
  useInteractiveRegion('desktop-dock-picker', pickerRef, element => {
    const rect = element.getBoundingClientRect()

    return new DOMRect(rect.left, rect.top, rect.width, rect.height + 12)
  })

  useLayoutEffect(() => {
    const positionPicker = (): void => {
      const anchor = (triggerRef.current ?? anchorRef.current)?.getBoundingClientRect()
      const width = Math.min(640, window.innerWidth - 32)
      const dockTop = anchor?.top ?? window.innerHeight - 82
      const center = anchor ? anchor.left + anchor.width / 2 : window.innerWidth / 2

      setPosition({
        bottom: window.innerHeight - dockTop + 12,
        left: Math.max(width / 2 + 16, Math.min(center, window.innerWidth - width / 2 - 16)),
        maxHeight: Math.max(0, Math.min(620, dockTop - 72))
      })
    }

    positionPicker()

    const observer = new ResizeObserver(positionPicker)

    if (anchorRef.current) {
      observer.observe(anchorRef.current)
    }

    if (triggerRef.current) {
      observer.observe(triggerRef.current)
    }

    window.addEventListener('resize', positionPicker)

    return () => {
      observer.disconnect()
      window.removeEventListener('resize', positionPicker)
    }
  }, [anchorRef, triggerRef])

  useEffect(() => {
    const outside = (event: PointerEvent): void => {
      if (
        event.target instanceof Node &&
        !pickerRef.current?.contains(event.target) &&
        !triggerRef.current?.contains(event.target)
      ) {
        onClose(false)
      }
    }

    const blur = (): void => {
      if (!browsingRef.current) {
        onClose(false)
      }
    }

    window.addEventListener('pointerdown', outside, true)
    window.addEventListener('blur', blur)

    return () => {
      window.removeEventListener('pointerdown', outside, true)
      window.removeEventListener('blur', blur)
    }
  }, [onClose, triggerRef])

  const trimmed = query.trim().toLowerCase()

  const visible = useMemo(() => {
    const collator = new Intl.Collator(locale === 'en' ? 'en' : 'zh-CN')

    return (catalog?.items ?? [])
      .filter(item => matches(item, trimmed))
      .sort((a, b) => collator.compare(a.name, b.name))
  }, [catalog, locale, trimmed])

  const loadCatalog = useCallback(
    async (force = false): Promise<void> => {
      const request = ++catalogRequestRef.current
      const isLive = beginAsync()

      if (force) {
        // 重扫废弃目录句柄，图标与选择不能沿用。
        catalogRevisionRef.current = undefined
        setCatalog(null)
        setIcons({})
        setActiveId(null)
        setFailed(false)
      }

      try {
        const next = await shellApi().dock.catalog(force, catalogRevisionRef.current)

        if (isLive() && request === catalogRequestRef.current) {
          const focusedRow = document.activeElement?.closest<HTMLElement>('[data-catalog-id]')

          restoreSearchFocusRef.current = Boolean(
            focusedRow &&
            listRef.current?.contains(focusedRow) &&
            !next.items.some(item => item.id === focusedRow.dataset.catalogId)
          )
          catalogRevisionRef.current = next.revision
          setCatalog(next)
          setFailed(false)
        }
      } catch (error) {
        if (isLive() && request === catalogRequestRef.current) {
          setFailed(true)
          notifyError(error, t.dock)
        }
      }
    },
    [beginAsync, t.dock]
  )

  useEffect(() => {
    void loadCatalog()
  }, [dockRevision, loadCatalog])

  useLayoutEffect(() => {
    if (restoreSearchFocusRef.current) {
      restoreSearchFocusRef.current = false

      if (document.activeElement === document.body) {
        searchRef.current?.focus()
      }
    }
  }, [catalog])

  useEffect(() => {
    const list = listRef.current

    if (!list) {
      setViewportIds([])

      return
    }

    const intersecting = new Set<string>()

    const observer = new IntersectionObserver(
      entries => {
        for (const entry of entries) {
          const id = (entry.target as HTMLElement).dataset.catalogId

          if (!id) {
            continue
          }

          if (entry.isIntersecting) {
            intersecting.add(id)
          } else {
            intersecting.delete(id)
          }
        }

        setViewportIds(visible.filter(item => intersecting.has(item.id)).map(item => item.id))
      },
      { root: list, rootMargin: '80px 0px' }
    )

    for (const item of list.children) {
      observer.observe(item)
    }

    return () => observer.disconnect()
  }, [visible])

  useEffect(() => {
    const pending = viewportIds.filter(id => !(id in icons)).slice(0, ICON_BATCH)

    if (!catalog || !pending.length) {
      return
    }

    let disposed = false
    const isLive = beginAsync()
    const scanRevision = catalog.revision

    const timer = window.setTimeout(() => {
      void shellApi()
        .dock.catalogIcons(pending)
        .then(next => {
          if (!disposed && isLive() && scanRevision === catalogRevisionRef.current) {
            setIcons(current => ({
              ...current,
              ...Object.fromEntries(pending.map(id => [id, next[id] ?? null]))
            }))
          }
        })
        .catch(() => {
          if (!disposed && isLive() && scanRevision === catalogRevisionRef.current) {
            // 图标失败只使用占位图，不阻塞程序启动或重复读取失败项。
            setIcons(current => ({ ...current, ...Object.fromEntries(pending.map(id => [id, null])) }))
          }
        })
    }, ICON_DEBOUNCE_MS)

    return () => {
      disposed = true
      window.clearTimeout(timer)
    }
  }, [beginAsync, catalog, icons, viewportIds])

  useEffect(() => {
    setActiveId(current => (current && visible.some(item => item.id === current) ? current : (visible[0]?.id ?? null)))
  }, [visible])

  const run = async (
    action: () => Promise<DockState | void>,
    closes: (next: DockState) => boolean = () => false
  ): Promise<void> => {
    if (busyRef.current || activating) {
      return
    }

    const isLive = beginAsync()
    busyRef.current = true
    setBusy(true)

    try {
      const next = await action()

      if (next && isLive()) {
        onCommitted(next, closes(next))
      }
    } catch (error) {
      if (isLive()) {
        notifyError(error, t.dock)
      }
    } finally {
      busyRef.current = false

      if (isLive()) {
        setBusy(false)
      }
    }
  }

  const choose = (item: CatalogItem | undefined): void => {
    if (!item || busyRef.current || activating) {
      return
    }

    if (mode.kind === 'browse') {
      onActivate(item.id)
    } else {
      void run(
        () => shellApi().dock.repairWithCatalog(mode.entryId, item.id),
        () => true
      )
    }
  }

  const pin = (item: CatalogItem): void => {
    if (!item.inDock) {
      void run(async () => {
        const isLive = beginAsync()
        const next = await shellApi().dock.addFromCatalog([item.id])

        if (isLive()) {
          setCatalog(current =>
            current
              ? {
                  ...current,
                  items: current.items.map(entry => (entry.id === item.id ? { ...entry, inDock: true } : entry))
                }
              : current
          )
        }

        return next
      })
    }
  }

  const browse = (): void => {
    void run(
      async () => {
        browsingRef.current = true

        try {
          return await (mode.kind === 'browse'
            ? shellApi().dock.addFromFiles()
            : shellApi().dock.repairWithFiles(mode.entryId))
        } finally {
          browsingRef.current = false
        }
      },
      next => mode.kind === 'repair' && next.pinnedRevision !== dockRevision
    )
  }

  const focusItem = (index: number): void => {
    const item = visible[index]
    const row = listRef.current?.children[index]

    if (item && row) {
      setActiveId(item.id)
      row.querySelector<HTMLButtonElement>('[data-catalog-launch]')?.focus()
      row.scrollIntoView({ block: 'nearest' })
    }
  }

  const navigate = (event: React.KeyboardEvent<HTMLUListElement>): void => {
    if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || !visible.length) {
      return
    }

    event.preventDefault()

    const current = Math.max(
      0,
      visible.findIndex(item => item.id === activeId)
    )

    const children = Array.from(listRef.current?.children ?? [])
    const firstTop = children[0]?.getBoundingClientRect().top

    const columns =
      mode.kind === 'browse'
        ? Math.max(1, children.filter(child => child.getBoundingClientRect().top === firstTop).length)
        : 1

    const delta =
      event.key === 'ArrowDown' ? columns : event.key === 'ArrowUp' ? -columns : event.key === 'ArrowLeft' ? -1 : 1

    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? visible.length - 1
          : Math.max(0, Math.min(visible.length - 1, current + delta))

    focusItem(next)
  }

  const unreadable = (catalog?.sources ?? []).filter(source => !source.ok)
  const status = catalog === null ? (failed ? t.pickLoadFailed : t.pickLoading) : null
  const disabled = busy || activating

  return (
    <div
      aria-label={mode.kind === 'browse' ? t.allApps : t.pickRepair}
      className={styles.dockPicker}
      data-mode={mode.kind}
      id="desktop-apps-panel"
      ref={pickerRef}
      role="dialog"
      style={{
        ...position,
        bottom: `max(${position.bottom}px, var(--desktop-popup-bottom))`,
        maxHeight: `min(${position.maxHeight}px, calc(100vh - max(${position.bottom}px, var(--desktop-popup-bottom)) - 16px))`
      }}
    >
      <header className={styles.dockPickerHeader}>
        <strong>{mode.kind === 'browse' ? t.allApps : t.pickRepair}</strong>
        <button aria-label={t.close} onClick={() => onClose(true)} title={t.close} type="button">
          <X size={15} />
        </button>
      </header>
      <label className={styles.dockPickerSearch}>
        <Search size={14} />
        <input
          aria-label={t.pickSearch}
          autoFocus
          onChange={event => setQuery(event.target.value)}
          onKeyDown={event => {
            if (event.nativeEvent.isComposing) {
              return
            }

            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault()
              focusItem(event.key === 'ArrowDown' ? 0 : visible.length - 1)
            } else if (event.key === 'Enter') {
              event.preventDefault()
              choose(visible.find(item => item.id === activeId) ?? visible[0])
            }
          }}
          placeholder={t.pickSearch}
          ref={searchRef}
          type="search"
          value={query}
        />
      </label>
      {unreadable.length > 0 && (
        <p className={styles.dockPickerNote}>
          {t.pickSourceFailed}：{unreadable.map(source => t.pickSources[source.key]).join('、')}
        </p>
      )}
      {status ? (
        <p className={styles.dockPickerStatus} role="status">
          {status}
        </p>
      ) : (
        <ul
          aria-busy={disabled}
          className={mode.kind === 'browse' ? styles.dockPickerGrid : styles.dockPickerList}
          onKeyDown={navigate}
          ref={listRef}
        >
          {visible.length === 0 ? (
            <li className={styles.dockPickerStatus}>{t.pickEmpty}</li>
          ) : (
            visible.map(item => (
              <li
                className={mode.kind === 'browse' ? styles.dockPickerTile : styles.dockPickerItem}
                data-active={item.id === activeId}
                data-catalog-id={item.id}
                key={item.id}
              >
                <button
                  aria-disabled={disabled}
                  aria-label={item.name}
                  className={styles.dockPickerLaunch}
                  data-catalog-launch
                  onClick={() => choose(item)}
                  onFocus={() => setActiveId(item.id)}
                  tabIndex={item.id === activeId ? 0 : -1}
                  title={`${item.name}\n${item.detail}`}
                  type="button"
                >
                  <span className={styles.dockPickerIcon}>
                    {icons[item.id] ? <img alt="" src={icons[item.id] ?? undefined} /> : <Monitor size={28} />}
                  </span>
                  <span className={styles.dockPickerText}>
                    <span className={styles.dockPickerName}>{item.name}</span>
                    {mode.kind === 'repair' && <span className={styles.dockPickerDetail}>{item.detail}</span>}
                  </span>
                </button>
                {mode.kind === 'browse' && (
                  <button
                    aria-disabled={disabled || item.inDock}
                    aria-label={`${item.inDock ? t.pickPinned : t.pinApp} · ${item.name}`}
                    className={styles.dockPickerPin}
                    onClick={() => pin(item)}
                    onFocus={() => setActiveId(item.id)}
                    tabIndex={item.id === activeId ? 0 : -1}
                    title={item.inDock ? t.pickPinned : t.pinApp}
                    type="button"
                  >
                    {item.inDock ? <Check size={12} /> : <Pin size={12} />}
                    {item.inDock ? t.pickPinned : t.pinApp}
                  </button>
                )}
              </li>
            ))
          )}
        </ul>
      )}
      <footer className={styles.dockPickerFooter}>
        <button
          disabled={disabled}
          onClick={() => {
            void run(() => loadCatalog(true))
          }}
          type="button"
        >
          <RefreshCw size={14} />
          {t.pickRescan}
        </button>
        <button disabled={disabled} onClick={browse} type="button">
          <FolderOpen size={14} />
          {mode.kind === 'browse' ? t.pickBrowsePin : t.pickBrowse}
        </button>
      </footer>
    </div>
  )
}
