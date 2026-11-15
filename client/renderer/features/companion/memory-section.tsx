import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { $systemPresets, fetchSystemPresets } from '@/features/conversation'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { requestResource } from '@/shared/lib/resource-request'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import { confirmAction, EmptyState, ErrorBanner, HINT_TEXT, LoadingBlock, Pager, SettingsSectionIntro } from '@/ui'
import type { CompanionMemoryBrowser } from '@client-shared/ipc/ui-state'
import type { MemoryCounts, MemoryItem } from '@protocol'

import {
  $companionEditors,
  completeMemoryDraft,
  editMemoryDraft,
  memoryBrowser,
  setMemoryDraft,
  updateCompanionEditors,
  updateMemoryBrowser
} from './companion-editor-state'
import { memoryCountBucket, MemoryItemCard } from './memory-item'
import { MemoryRecallFilters, type MemorySectionId, MemoryToolbar } from './memory-toolbar'
import { useMemoryChanged, UserProfileSection } from './user-profile-section'

const PAGE_SIZE = 20

export function MemorySection(): React.ReactElement {
  const presets = useStore($systemPresets)
  const memory = useStore($companionEditors).memory
  const dict = useStrings()
  const persona = dict.settings.persona
  const { presetId, section } = memory

  useEffect(() => {
    void fetchSystemPresets()
  }, [])

  const showProfile = section === 'profile' && presetId === 'companion'

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="shrink-0">
        <SettingsSectionIntro hint={persona.memoryIntro} title={persona.memoriesTitle} />
      </div>
      <MemoryToolbar
        onChangePreset={next =>
          updateCompanionEditors({
            memory: {
              ...$companionEditors.get().memory,
              presetId: next,
              section: next === 'companion' ? section : 'recall'
            }
          })
        }
        onChangeSection={(next: MemorySectionId) =>
          updateCompanionEditors({
            memory: { ...$companionEditors.get().memory, section: next }
          })
        }
        presetId={presetId}
        presets={presets}
        section={section}
      />
      {showProfile ? <UserProfileSection /> : <RecallBrowser key={presetId} presetId={presetId} />}
    </div>
  )
}

function RecallBrowser({ presetId }: { presetId: string }): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.memory
  const memory = useStore($companionEditors).memory
  const browser = memory.browsers[presetId] ?? memoryBrowser(presetId)
  const { status, page, query } = browser
  const cursor = browser.cursors[page]
  const [rows, setRows] = useState<MemoryItem[]>([])
  const [counts, setCounts] = useState<MemoryCounts | null>(null)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [hint, setHint] = useState<string | null>(null)
  const [savedHint, setSavedHint] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // 就地编辑的选中与展开随组件生命周期，不跨分区保留。
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const loadId = useRef(0)
  const listRef = useRef<HTMLDivElement>(null)
  const editorRef = useRef<HTMLElement>(null)
  const begin = useAsyncGuard()

  const load = useCallback(
    async (background = false): Promise<void> => {
      const sameFilter = (): boolean => {
        const current = memoryBrowser(presetId)

        return current.status === status && current.page === page && current.query === query
      }

      if (!sameFilter()) {
        return
      }

      const isLive = begin()
      const requestId = ++loadId.current

      if (!background) {
        setLoading(true)
        setRows([])
      }

      setHint(null)

      try {
        const result = await requestResource('resource_catalog.get_memories', {
          query: {
            kind: 'recall',
            status,
            system_preset_id: presetId,
            q: query.trim(),
            cursor,
            limit: PAGE_SIZE
          }
        })

        if (!isLive() || !sameFilter() || requestId !== loadId.current || result.system_preset_id !== presetId) {
          return
        }

        setRows(result.memories)
        setCounts(result.counts)
        setNextCursor(result.next_cursor ?? null)

        if (page > 0 && !result.memories.length) {
          updateMemoryBrowser(presetId, { page: page - 1 })
          setSelectedId(null)
          setEditorOpen(false)
        }
      } catch (error) {
        if (!isLive() || requestId !== loadId.current) {
          return
        }

        setHint(t.loadFailedHint)
        notifyError(error, t.loadFailedToast)
      } finally {
        if (isLive() && requestId === loadId.current) {
          setLoading(false)
        }
      }
    },
    [begin, cursor, page, presetId, query, status, t.loadFailedHint, t.loadFailedToast]
  )

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 200)

    return () => {
      window.clearTimeout(timeout)
      loadId.current += 1
    }
  }, [load])
  const refresh = useCallback(() => load(true), [load])
  useMemoryChanged(presetId, refresh)

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = memoryBrowser(presetId).scrollTop
    }
  }, [loading, presetId])

  const selected = rows.find(row => row.id === selectedId) ?? rows[0]

  const changeFilter = (filter: Partial<Pick<CompanionMemoryBrowser, 'page' | 'query' | 'status'>>): void => {
    if (listRef.current) {
      listRef.current.scrollTop = 0
    }

    setSavedHint(null)
    const paging = filter.page !== undefined
    updateMemoryBrowser(presetId, {
      page: 0,
      ...filter,
      cursors: paging ? browser.cursors : [null],
      scrollTop: 0
    })
    setSelectedId(null)
    setEditorOpen(false)
  }

  const selectRow = (id: string): void => {
    setSelectedId(id)
    setEditorOpen(true)
    setSavedHint(null)
    window.requestAnimationFrame(() => {
      const list = listRef.current
      const editor = editorRef.current

      if (list && editor) {
        list.scrollTop += editor.getBoundingClientRect().top - list.getBoundingClientRect().top
      }

      document.getElementById(`memory-${id}`)?.focus({ preventScroll: true })
    })
  }

  const closeEditor = (): void => {
    setEditorOpen(false)
    window.requestAnimationFrame(() => {
      listRef.current
        ?.querySelector<HTMLButtonElement>(`[data-memory-id="${selected?.id}"]`)
        ?.focus({ preventScroll: true })
    })
  }

  const save = async (id: string): Promise<void> => {
    const target = rows.find(row => row.id === id)
    const targetDraft = memory.drafts[`${presetId}:${id}`]

    if (!target || !targetDraft || busy) {
      return
    }

    const targetKey = `${presetId}:${id}`
    const isLive = begin()
    const isCurrent = captureAuthScope()

    if (!isCurrent) {
      return
    }

    setBusy(true)
    setHint(null)
    setSavedHint(null)

    try {
      const updated = await requestResource('resource_catalog.patch_memory', {
        path: { memory_id: target.id },
        body: {
          content: targetDraft.content.trim(),
          expected_version: targetDraft.baseVersion,
          system_preset_id: presetId
        }
      })

      if (!isCurrent()) {
        return
      }

      completeMemoryDraft('drafts', targetKey, targetDraft, updated)

      if (!isLive()) {
        return
      }

      setSavedHint(status === 'active' ? t.saved : dict.settings.persona.memorySavedActive)

      // 行就地更新：patch 响应已含完整行，不必整页重读；计数桶变化时本地同步。
      const previous = rows.find(row => row.id === updated.id)

      setRows(current =>
        current.map(entry =>
          entry.id === updated.id && entry.content_version <= updated.content_version ? updated : entry
        )
      )

      const previousBucket = previous ? memoryCountBucket(previous) : null
      const nextBucket = memoryCountBucket(updated)

      if (previousBucket && nextBucket) {
        if (previousBucket !== nextBucket) {
          setCounts(current =>
            current
              ? {
                  ...current,
                  [nextBucket]: (current[nextBucket] ?? 0) + 1,
                  [previousBucket]: (current[previousBucket] ?? 0) - 1
                }
              : current
          )
        }
      } else {
        // 桶归属判定不了时回源一次，不猜计数。
        void load(true)
      }
    } catch (error) {
      if (!isCurrent()) {
        return
      }

      let confirmed: MemoryItem | undefined

      try {
        const result = await requestResource('resource_catalog.get_memories', {
          query: {
            kind: 'recall',
            status: 'active',
            system_preset_id: presetId,
            q: targetDraft.content.trim(),
            limit: PAGE_SIZE
          }
        })

        confirmed = result.memories.find(
          row =>
            row.id === target.id &&
            row.content === targetDraft.content.trim() &&
            row.content_version > targetDraft.baseVersion
        )
      } catch {
        // 原写请求的错误仍交给页面呈现，读取失败时保留原稿与版本。
      }

      if (!isCurrent()) {
        return
      }

      if (confirmed) {
        completeMemoryDraft('drafts', targetKey, targetDraft, confirmed)
      }

      if (isLive()) {
        await load(true)

        if (isLive()) {
          if (confirmed) {
            setSavedHint(status === 'active' ? t.saved : dict.settings.persona.memorySavedActive)
          } else {
            notifyError(error, t.saveFailedToast)
            setHint(t.saveFailedHint)
          }
        }
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  const forget = async (id: string): Promise<void> => {
    const target = rows.find(row => row.id === id)

    if (!target || busy) {
      return
    }

    const isLive = begin()
    const isCurrent = captureAuthScope()

    if (!isCurrent) {
      return
    }

    if (
      !(await confirmAction({
        title: dict.settings.persona.memoryForgetTitle,
        description: target.content ?? '',
        confirmLabel: dict.common.forget,
        variant: 'destructive'
      })) ||
      !isLive()
    ) {
      return
    }

    setBusy(true)

    try {
      await requestResource('resource_catalog.delete_memory', {
        path: { memory_id: target.id },
        query: { system_preset_id: presetId }
      })

      if (!isCurrent()) {
        return
      }

      setMemoryDraft('drafts', `${presetId}:${id}`, undefined)

      if (!isLive()) {
        return
      }

      setSelectedId(null)
      setEditorOpen(false)
      await load(true)
    } catch (error) {
      if (isLive()) {
        setHint(t.deleteFailedHint)
        notifyError(error, t.deleteFailedToast)
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <MemoryRecallFilters
        counts={counts}
        onChangeQuery={next => changeFilter({ query: next })}
        onChangeStatus={next => changeFilter({ status: next })}
        query={query}
        status={status}
      />
      {hint ? <ErrorBanner message={hint} onRetry={() => void load()} retryLabel={dict.common.retry} /> : null}
      {savedHint ? (
        <p aria-live="polite" className={HINT_TEXT}>
          {savedHint}
        </p>
      ) : null}
      {loading ? (
        <LoadingBlock label={t.loading} />
      ) : rows.length === 0 ? (
        !hint ? (
          <EmptyState title={query ? dict.settings.persona.memoryNoMatch : t.empty} />
        ) : null
      ) : (
        <>
          <div
            className="min-h-0 flex-1 space-y-2 overflow-y-auto overscroll-contain"
            onScroll={event => updateMemoryBrowser(presetId, { scrollTop: event.currentTarget.scrollTop })}
            ref={listRef}
          >
            {rows.map(row => {
              const editing = editorOpen && selected?.id === row.id
              const rowKey = `${presetId}:${row.id}`
              const rowDraft = memory.drafts[rowKey]

              return (
                <MemoryItemCard
                  busy={busy}
                  cardRef={editing ? editorRef : undefined}
                  draft={rowDraft}
                  editing={editing}
                  filterStatus={status}
                  key={row.id}
                  onChange={value => {
                    setSavedHint(null)
                    setMemoryDraft('drafts', rowKey, editMemoryDraft(rowDraft, value, row))
                  }}
                  onClose={closeEditor}
                  onForget={() => void forget(row.id)}
                  onKeepMine={() => {
                    if (rowDraft) {
                      setMemoryDraft('drafts', rowKey, {
                        ...rowDraft,
                        baseContent: row.content ?? '',
                        baseVersion: row.content_version
                      })
                    }
                  }}
                  onReset={() => setMemoryDraft('drafts', rowKey, undefined)}
                  onSave={() => void save(row.id)}
                  onSelect={() => selectRow(row.id)}
                  presetId={presetId}
                  row={row}
                />
              )
            })}
          </div>
          <Pager
            hasMore={Boolean(nextCursor)}
            onPageChange={next => {
              if (next > page && nextCursor) {
                updateMemoryBrowser(presetId, {
                  cursors: [...browser.cursors.slice(0, page + 1), nextCursor],
                  page: next,
                  scrollTop: 0
                })
                setSelectedId(null)
                setEditorOpen(false)
              } else if (next < page) {
                changeFilter({ page: next })
              }
            }}
            page={page}
          />
        </>
      )}
    </div>
  )
}
