import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { Check, FolderOpen, Monitor, RefreshCw, Search, X } from '@/shared/lib/icons'
import { useInteractiveRegion } from '@/shared/lib/interactive-regions'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'

import { useDesktopStrings } from './desktop-strings'
import styles from './desktop.module.css'

type Catalog = Awaited<ReturnType<typeof window.spiritagent.dock.catalog>>
type CatalogItem = Catalog['items'][number]
type DockState = Awaited<ReturnType<typeof window.spiritagent.dock.getState>>

/** 面板一次最多为前 60 条结果取图标；图标陆续到位后自动续取下一批。 */
const ICON_BATCH = 60
const ICON_DEBOUNCE_MS = 120

export type DockPickerMode = { kind: 'add' } | { kind: 'repair'; entryId: string }

function matches(item: CatalogItem, query: string): boolean {
  return item.name.toLowerCase().includes(query) || item.detail.toLowerCase().includes(query)
}

export function DesktopDockPicker({
  dockRevision,
  mode,
  onClose,
  onCommitted
}: {
  /** 固定配置版本：窗口与图标更新不触发目录查询。 */
  dockRevision: number
  mode: DockPickerMode
  onClose: () => void
  /** `close` 表示本次动作已产生结果、面板可以收起；取消与无变化保留面板。 */
  onCommitted: (state: DockState, close: boolean) => void
}): React.JSX.Element {
  const t = useDesktopStrings()
  const locale = useStore($locale)
  const beginAsync = useAsyncGuard()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [failed, setFailed] = useState(false)
  const [query, setQuery] = useState('')
  const [icons, setIcons] = useState<Record<string, string | null>>({})
  const [activeId, setActiveId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const listRef = useRef<HTMLUListElement>(null)
  const pickerRef = useRef<HTMLDivElement>(null)
  const browsingRef = useRef(false)
  useInteractiveRegion('desktop-dock-picker', pickerRef)

  useEffect(() => {
    const outside = (event: PointerEvent): void => {
      if (event.target instanceof Node && !pickerRef.current?.contains(event.target)) {
        onClose()
      }
    }

    const blur = (): void => {
      if (!browsingRef.current) {
        onClose()
      }
    }

    window.addEventListener('pointerdown', outside, true)
    window.addEventListener('blur', blur)

    return () => {
      window.removeEventListener('pointerdown', outside, true)
      window.removeEventListener('blur', blur)
    }
  }, [onClose])

  const trimmed = query.trim().toLowerCase()

  const visible = useMemo(() => {
    const collator = new Intl.Collator(locale === 'en' ? 'en' : 'zh-CN')

    return (catalog?.items ?? [])
      .filter(item => matches(item, trimmed))
      .sort((a, b) => Number(a.inDock) - Number(b.inDock) || collator.compare(a.name, b.name))
  }, [catalog, locale, trimmed])

  useEffect(() => {
    let disposed = false
    const isLive = beginAsync()

    void window.spiritagent.dock
      .catalog()
      .then(next => {
        if (!disposed && isLive()) {
          setCatalog(next)
          setFailed(false)
        }
      })
      .catch(error => {
        if (!disposed && isLive()) {
          setFailed(true)
        }

        notifyError(error, t.dock)
      })

    return () => {
      disposed = true
    }
  }, [beginAsync, dockRevision, t.dock])

  useEffect(() => {
    if (!catalog) {
      return
    }

    const pending = visible.filter(item => !(item.id in icons)).slice(0, ICON_BATCH)

    if (!pending.length) {
      return
    }

    let disposed = false
    const isLive = beginAsync()

    const timer = window.setTimeout(() => {
      void window.spiritagent.dock
        .catalogIcons(pending.map(item => item.id))
        .then(next => {
          if (!disposed && isLive()) {
            setIcons(current => ({ ...current, ...next }))
          }
        })
        // 图标失败只影响外观，条目照常可选。
        .catch(() => undefined)
    }, ICON_DEBOUNCE_MS)

    return () => {
      disposed = true
      window.clearTimeout(timer)
    }
  }, [beginAsync, catalog, icons, visible])

  useEffect(() => {
    setActiveId(current => (current && visible.some(item => item.id === current) ? current : null))
  }, [visible])

  // 修复模式下一律可选：失效条目对应的程序正需要被选中重选，重复由主进程判定。
  const locked = (item: CatalogItem): boolean => mode.kind === 'add' && item.inDock

  const run = async (
    action: () => Promise<DockState | void>,
    // 面板何时收起由调用点决定：添加后保留，修复或文件对话框有结果时收起。
    closes: (next: DockState) => boolean = () => false
  ): Promise<void> => {
    if (busy) {
      return
    }

    const isLive = beginAsync()

    setBusy(true)

    try {
      const next = await action()

      if (next && isLive()) {
        onCommitted(next, closes(next))
      }
    } catch (error) {
      if (isLive()) {
        // 提交失败保留面板，用户可改选或重试。
        notifyError(error, t.dock)
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  const choose = (item: CatalogItem | undefined): void => {
    if (!item || locked(item)) {
      return
    }

    void run(
      () =>
        mode.kind === 'add'
          ? window.spiritagent.dock.addFromCatalog([item.id])
          : window.spiritagent.dock.repairWithCatalog(mode.entryId, item.id),
      () => mode.kind === 'repair'
    )
  }

  const rescan = (): void => {
    void run(async () => {
      // 重扫后旧句柄失效，本地图标缓存一并作废。
      setCatalog(null)
      setIcons({})
      setFailed(false)

      try {
        setCatalog(await window.spiritagent.dock.catalog(true))
        setFailed(false)
      } catch (error) {
        setFailed(true)

        throw error
      }
    })
  }

  const browse = (): void => {
    // 取消文件对话框时配置未变，保留面板让用户改从列表挑选。
    void run(
      async () => {
        browsingRef.current = true

        try {
          return await (mode.kind === 'add'
            ? window.spiritagent.dock.addFromFiles()
            : window.spiritagent.dock.repairWithFiles(mode.entryId))
        } finally {
          browsingRef.current = false
        }
      },
      next => next.pinnedRevision !== dockRevision
    )
  }

  const step = (delta: number): void => {
    if (!visible.length) {
      return
    }

    const current = visible.findIndex(item => item.id === activeId)
    const next = (current + delta + visible.length) % visible.length

    setActiveId(visible[next].id)
    listRef.current?.children[next]?.scrollIntoView({ block: 'nearest' })
  }

  const unreadable = (catalog?.sources ?? []).filter(source => !source.ok)
  const status = catalog === null ? (failed ? t.pickLoadFailed : t.pickLoading) : null

  return (
    <div className={styles.dockPickerScrim}>
      <div
        aria-label={mode.kind === 'add' ? t.pickAdd : t.pickRepair}
        className={styles.dockPicker}
        ref={pickerRef}
        role="dialog"
      >
        <header className={styles.dockPickerHeader}>
          <strong>{mode.kind === 'add' ? t.pickAdd : t.pickRepair}</strong>
          <button aria-label={t.pickCancel} onClick={onClose} title={t.pickCancel} type="button">
            <X size={15} />
          </button>
        </header>
        <label className={styles.dockPickerSearch}>
          <Search size={14} />
          <input
            autoFocus
            onChange={event => setQuery(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'ArrowDown') {
                event.preventDefault()
                step(1)
              } else if (event.key === 'ArrowUp') {
                event.preventDefault()
                step(-1)
              } else if (event.key === 'Enter') {
                event.preventDefault()
                choose(visible.find(item => item.id === activeId) ?? visible[0])
              }
            }}
            placeholder={t.pickSearch}
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
          <p className={styles.dockPickerStatus}>{status}</p>
        ) : (
          <ul className={styles.dockPickerList} ref={listRef} role="listbox">
            {visible.length === 0 ? (
              <li className={styles.dockPickerStatus}>{t.pickEmpty}</li>
            ) : (
              visible.map(item => (
                <li
                  aria-disabled={locked(item)}
                  aria-selected={item.id === activeId}
                  className={styles.dockPickerItem}
                  data-active={item.id === activeId}
                  data-added={locked(item)}
                  key={item.id}
                  onClick={() => choose(item)}
                  onMouseEnter={() => setActiveId(item.id)}
                  role="option"
                >
                  <span className={styles.dockPickerIcon}>
                    {icons[item.id] ? <img alt="" src={icons[item.id] ?? undefined} /> : <Monitor size={22} />}
                  </span>
                  <span className={styles.dockPickerText}>
                    <span className={styles.dockPickerName}>{item.name}</span>
                    <span className={styles.dockPickerDetail}>{item.detail}</span>
                  </span>
                  {locked(item) && (
                    <span className={styles.dockPickerAdded}>
                      <Check size={12} />
                      {t.pickAdded}
                    </span>
                  )}
                </li>
              ))
            )}
          </ul>
        )}
        <footer className={styles.dockPickerFooter}>
          <button disabled={busy} onClick={browse} type="button">
            <FolderOpen size={14} />
            {t.pickBrowse}
          </button>
          <button disabled={busy} onClick={rescan} type="button">
            <RefreshCw size={14} />
            {t.pickRescan}
          </button>
          <button disabled={busy} onClick={onClose} type="button">
            {t.pickCancel}
          </button>
        </footer>
      </div>
    </div>
  )
}
