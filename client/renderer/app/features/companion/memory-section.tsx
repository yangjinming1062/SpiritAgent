import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { $systemPresets, fetchSystemPresets, presetDisplayName } from '@/modules/conversation'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { requestResource } from '@/shared/lib/resource-request'
import { safeJsonParse } from '@/shared/lib/safe-json'
import { cn } from '@/shared/lib/utils'
import {
  BTN_GHOST,
  BTN_SUBTLE,
  CapsuleTabs,
  CHIP,
  confirmAction,
  Disclosure,
  ErrorBanner,
  HINT_TEXT,
  INPUT_CLASS,
  PanelSelect
} from '@/shared/panel'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
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
import { useMemoryChanged, UserProfileSection } from './user-profile-section'

const PAGE_SIZE = 20

export function MemorySection(): React.ReactElement {
  const presets = useStore($systemPresets)
  const memory = useStore($companionEditors).memory
  const dict = useStrings()
  const { presetId, section } = memory
  useEffect(() => {
    void fetchSystemPresets()
  }, [])

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex shrink-0 flex-wrap items-end gap-3">
        <label className="block min-w-40 flex-1 text-sm text-body">
          {dict.settings.persona.memoryScope}
          <PanelSelect
            ariaLabel={dict.settings.memory.presetLabel}
            onChange={next =>
              updateCompanionEditors({
                memory: {
                  ...$companionEditors.get().memory,
                  presetId: next,
                  section: next === 'companion' ? section : 'recall'
                }
              })
            }
            options={presets.map(preset => ({ value: preset.id, label: presetDisplayName(dict, preset) }))}
            value={presetId}
            widthClass="w-full"
          />
        </label>
        {presetId === 'companion' && (
          <CapsuleTabs
            ariaLabel={dict.settings.persona.memoryScopeAria}
            onChange={next => updateCompanionEditors({ memory: { ...$companionEditors.get().memory, section: next } })}
            options={[
              { value: 'recall', label: dict.settings.persona.memoryKindRecall },
              { value: 'profile', label: dict.settings.memory.profile.title }
            ]}
            value={section}
          />
        )}
      </div>
      {section === 'profile' && presetId === 'companion' ? (
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <UserProfileSection />
        </div>
      ) : (
        <RecallBrowser key={presetId} presetId={presetId} />
      )}
    </section>
  )
}

function RecallBrowser({ presetId }: { presetId: string }): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.memory
  const locale = useStore($locale)
  const memory = useStore($companionEditors).memory
  const browser = memory.browsers[presetId] ?? memoryBrowser(presetId)
  const { status, page, query } = browser
  const [rows, setRows] = useState<MemoryItem[]>([])
  const [counts, setCounts] = useState<MemoryCounts | null>(null)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [hint, setHint] = useState<string | null>(null)
  const [savedHint, setSavedHint] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
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
            offset: page * PAGE_SIZE,
            limit: PAGE_SIZE
          }
        })

        if (!isLive() || !sameFilter() || requestId !== loadId.current || result.system_preset_id !== presetId) {
          return
        }

        setRows(result.memories)
        setCounts(result.counts)
        setTotal(result.total)

        if (page > 0 && page * PAGE_SIZE >= result.total) {
          updateMemoryBrowser(presetId, {
            page: Math.max(0, Math.ceil(result.total / PAGE_SIZE) - 1),
            selectedId: null,
            editorOpen: false
          })
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
    [begin, page, presetId, query, status, t.loadFailedHint, t.loadFailedToast]
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

  const selected = rows.find(row => row.id === browser.selectedId) ?? rows[0]
  const draftKey = selected ? `${presetId}:${selected.id}` : ''
  const draft = memory.drafts[draftKey]
  const conflict = !!draft && selected?.content_version !== draft.baseVersion

  const changeFilter = (filter: Partial<Pick<CompanionMemoryBrowser, 'page' | 'query' | 'status'>>): void => {
    if (listRef.current) {
      listRef.current.scrollTop = 0
    }

    setSavedHint(null)
    updateMemoryBrowser(presetId, { page: 0, ...filter, selectedId: null, editorOpen: false, scrollTop: 0 })
  }

  const selectRow = (id: number): void => {
    updateMemoryBrowser(presetId, { selectedId: id, editorOpen: true })
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
    updateMemoryBrowser(presetId, { editorOpen: false })
    window.requestAnimationFrame(() => {
      listRef.current
        ?.querySelector<HTMLButtonElement>(`[data-memory-id="${selected?.id}"]`)
        ?.focus({ preventScroll: true })
    })
  }

  const save = async (): Promise<void> => {
    if (!selected || !draft || busy) {
      return
    }

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
        path: { memory_id: selected.id },
        body: {
          content: draft.content.trim(),
          expected_version: draft.baseVersion,
          system_preset_id: presetId
        }
      })

      if (!isCurrent()) {
        return
      }

      completeMemoryDraft('drafts', draftKey, draft, updated)

      if (!isLive()) {
        return
      }

      setSavedHint(status === 'active' ? t.saved : dict.settings.persona.memorySavedActive)
      await load(true)
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
            q: draft.content.trim(),
            limit: PAGE_SIZE
          }
        })

        confirmed = result.memories.find(
          row =>
            row.id === selected.id && row.content === draft.content.trim() && row.content_version > draft.baseVersion
        )
      } catch {
        // 原写请求的错误仍交给页面呈现，读取失败时保留原稿与版本。
      }

      if (!isCurrent()) {
        return
      }

      if (confirmed) {
        completeMemoryDraft('drafts', draftKey, draft, confirmed)
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

  const forget = async (): Promise<void> => {
    if (!selected || busy) {
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
        description: selected.content ?? '',
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
        path: { memory_id: selected.id },
        query: { system_preset_id: presetId }
      })

      if (!isCurrent()) {
        return
      }

      setMemoryDraft('drafts', draftKey, undefined)

      if (!isLive()) {
        return
      }

      updateMemoryBrowser(presetId, { selectedId: null, editorOpen: false })
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
      <div className="shrink-0 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <label className="block min-w-40 flex-1">
            <span className="sr-only">{dict.settings.persona.memorySearchAria}</span>
            <input
              className={INPUT_CLASS}
              onChange={event => changeFilter({ query: event.target.value })}
              placeholder={dict.settings.persona.memorySearchPlaceholder}
              type="search"
              value={query}
            />
          </label>
          <CapsuleTabs
            ariaLabel={t.tabAriaLabel}
            onChange={next => changeFilter({ status: next })}
            options={[
              { label: t.tabActive(counts?.active ?? '…'), value: 'active' },
              { label: t.tabCandidate(counts?.candidate ?? '…'), value: 'candidate' },
              { label: t.tabInvalidated(counts?.invalidated ?? '…'), value: 'invalidated' },
              { label: t.tabExpired(counts?.expired ?? '…'), value: 'expired' }
            ]}
            size="sm"
            value={status}
          />
        </div>
        <p className={HINT_TEXT}>{t.maintenanceHint}</p>
        {hint && <ErrorBanner message={hint} onRetry={() => void load()} retryLabel={dict.common.retry} />}
        {savedHint && (
          <p aria-live="polite" className={HINT_TEXT}>
            {savedHint}
          </p>
        )}
      </div>
      {loading ? (
        <p className={HINT_TEXT}>{t.loading}</p>
      ) : rows.length === 0 ? (
        !hint && <p className={HINT_TEXT}>{query ? dict.settings.persona.memoryNoMatch : t.empty}</p>
      ) : (
        <>
          <div
            className="min-h-0 flex-1 space-y-2 overflow-y-auto overscroll-contain"
            onScroll={event => updateMemoryBrowser(presetId, { scrollTop: event.currentTarget.scrollTop })}
            ref={listRef}
          >
            {rows.map(row => {
              const editing = browser.editorOpen && selected?.id === row.id

              return (
                <article
                  className={cn(
                    'liquid-glass-card rounded-xl',
                    editing && 'outline-1 -outline-offset-1 outline-accent'
                  )}
                  key={row.id}
                  ref={editing ? editorRef : undefined}
                >
                  {editing ? (
                    <div className="space-y-3 p-4">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <label className="text-sm font-medium text-strong" htmlFor={`memory-${row.id}`}>
                          {dict.settings.persona.memoryEdit}
                        </label>
                        <div className="flex items-center gap-2">
                          {draft && <span className={HINT_TEXT}>{dict.common.unsavedChanges}</span>}
                          <button className={BTN_GHOST} onClick={closeEditor} type="button">
                            {dict.settings.persona.memoryCollapse}
                          </button>
                        </div>
                      </div>
                      <textarea
                        className={cn(INPUT_CLASS, 'min-h-40 resize-y')}
                        disabled={busy}
                        id={`memory-${row.id}`}
                        onChange={event => {
                          setSavedHint(null)
                          setMemoryDraft('drafts', draftKey, editMemoryDraft(draft, event.target.value, row))
                        }}
                        rows={7}
                        value={draft?.content ?? row.content ?? ''}
                      />
                      {status !== 'active' && <p className={HINT_TEXT}>{dict.settings.persona.memorySaveHint}</p>}
                      {conflict && (
                        <div className="space-y-2 rounded-xl border border-line-hairline p-3">
                          <p className="text-xs text-amber-300/90">{dict.settings.persona.memoryLatestContent}</p>
                          <p className="whitespace-pre-wrap break-words text-sm text-body">{row.content}</p>
                          <button
                            className={BTN_SUBTLE}
                            disabled={busy}
                            onClick={() => {
                              if (draft) {
                                setMemoryDraft('drafts', draftKey, {
                                  ...draft,
                                  baseContent: row.content ?? '',
                                  baseVersion: row.content_version
                                })
                              }
                            }}
                            type="button"
                          >
                            {dict.common.keepMyEdits}
                          </button>
                        </div>
                      )}
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          className={BTN_SUBTLE}
                          disabled={busy || !draft?.content.trim() || conflict}
                          onClick={() => void save()}
                          type="button"
                        >
                          {busy
                            ? dict.common.saving
                            : status === 'active'
                              ? dict.common.saveChanges
                              : dict.settings.persona.memorySaveAgain}
                        </button>
                        <button
                          className={BTN_GHOST}
                          disabled={busy || !draft}
                          onClick={() => setMemoryDraft('drafts', draftKey, undefined)}
                          type="button"
                        >
                          {dict.common.cancel}
                        </button>
                        <button
                          className={cn(BTN_GHOST, 'text-danger-fg')}
                          disabled={busy}
                          onClick={() => void forget()}
                          type="button"
                        >
                          {dict.settings.persona.memoryForgetAction}
                        </button>
                      </div>
                      <p className={cn(HINT_TEXT, 'break-words')}>
                        {row.context?.replace(/^recall:/, '') || '—'} · {t.updated} {row.updated_at ?? '—'}
                      </p>
                      <MemoryDetails row={row} />
                    </div>
                  ) : (
                    <button
                      aria-expanded={false}
                      className="w-full rounded-xl p-3 text-left focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2"
                      data-memory-id={row.id}
                      onClick={() => selectRow(row.id)}
                      type="button"
                    >
                      <p className="line-clamp-3 whitespace-pre-wrap break-words text-sm leading-6 text-body">
                        {row.content}
                      </p>
                      <p className={cn(HINT_TEXT, 'mt-2 break-words')}>
                        {row.context?.replace(/^recall:/, '') || '—'} ·{' '}
                        {row.updated_at ? new Date(row.updated_at).toLocaleDateString(locale) : '—'}
                      </p>
                      {memory.drafts[`${presetId}:${row.id}`] && (
                        <span className="text-xs text-accent">{dict.common.unsavedChanges}</span>
                      )}
                    </button>
                  )}
                </article>
              )
            })}
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2">
            <button
              className={BTN_SUBTLE}
              disabled={page === 0}
              onClick={() => changeFilter({ page: page - 1 })}
              type="button"
            >
              {dict.common.previous}
            </button>
            <span className={HINT_TEXT}>
              {page + 1} / {Math.max(1, Math.ceil(total / PAGE_SIZE))} · {dict.settings.persona.memoryTotal(total)}
            </span>
            <button
              className={BTN_SUBTLE}
              disabled={(page + 1) * PAGE_SIZE >= total}
              onClick={() => changeFilter({ page: page + 1 })}
              type="button"
            >
              {dict.common.next}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function MemoryDetails({ row }: { row: MemoryItem }): React.ReactElement {
  const t = useStrings().settings.memory
  const parsed = safeJsonParse<unknown>(row.tags, [])
  const tags = Array.isArray(parsed) ? parsed.map(String) : []

  return (
    <Disclosure
      bodyClassName="space-y-2"
      className="border-t border-line-hairline pt-3 text-xs text-muted"
      title={t.evidence}
      variant="inline"
    >
      <p>
        {t.basis[row.basis]} · {t.usage[row.usage]}
      </p>
      {row.reason && <p className="whitespace-pre-wrap break-words">{row.reason}</p>}
      {row.expires_at && (
        <p>
          {t.expires} {row.expires_at}
        </p>
      )}
      <div className="flex flex-wrap gap-1">
        {tags.map(tag => (
          <span className={CHIP} key={tag}>
            {tag}
          </span>
        ))}
      </div>
      {row.evidence.map((entry, index) => (
        <blockquote className="border-l border-line-hairline pl-2" key={`${entry.fingerprint}-${index}`}>
          {entry.stance ? t.stance[entry.stance] : ''} · {entry.created_at}
          <br />
          {entry.quote}
        </blockquote>
      ))}
    </Disclosure>
  )
}
