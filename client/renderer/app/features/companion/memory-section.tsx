import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { $systemPresets, fetchSystemPresets, presetDisplayName } from '@/modules/conversation'
import { requestGateway } from '@/shared'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import { safeJsonParse } from '@/shared/lib/safe-json'
import { cn } from '@/shared/lib/utils'
import {
  BTN_GHOST,
  BTN_SUBTLE,
  CapsuleTabs,
  CHIP,
  confirmAction,
  HINT_TEXT,
  INPUT_CLASS,
  PanelSelect
} from '@/shared/panel'
import { $locale } from '@/shared/store/locale'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { MemoryCounts, MemoryItem, MemoryListResponse } from '@protocol'

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
  const english = useStore($locale) === 'en'
  const dict = useStrings()
  const { presetId, section } = memory
  useEffect(() => {
    void fetchSystemPresets()
  }, [])

  return (
    <section className="space-y-4">
      <label className="block text-sm text-body">
        {english ? 'Whose memories' : '查看谁的记忆'}
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
          ariaLabel={english ? 'Memory type' : '记忆类型'}
          onChange={next => updateCompanionEditors({ memory: { ...$companionEditors.get().memory, section: next } })}
          options={[
            { value: 'recall', label: english ? 'Remembered things' : '记住的事' },
            { value: 'profile', label: dict.settings.memory.profile.title }
          ]}
          value={section}
        />
      )}
      {section === 'profile' && presetId === 'companion' ? (
        <UserProfileSection />
      ) : (
        <RecallBrowser key={presetId} presetId={presetId} />
      )}
    </section>
  )
}

function RecallBrowser({ presetId }: { presetId: string }): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.memory
  const english = useStore($locale) === 'en'
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
  const contentRef = useRef<HTMLDivElement>(null)
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
        const result = await requestGateway<MemoryListResponse>('memory.list', {
          kind: 'recall',
          status,
          system_preset_id: presetId,
          q: query.trim(),
          offset: page * PAGE_SIZE,
          limit: PAGE_SIZE
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
  }, [browser.editorOpen, loading, presetId])

  const selected = rows.find(row => row.id === browser.selectedId) ?? rows[0]
  const draftKey = selected ? `${presetId}:${selected.id}` : ''
  const draft = memory.drafts[draftKey]
  const conflict = !!draft && selected?.content_version !== draft.baseVersion

  const pageScroller = (): HTMLElement | null => {
    let element = contentRef.current?.parentElement ?? null

    while (element) {
      if (['auto', 'scroll'].includes(getComputedStyle(element).overflowY)) {
        return element
      }

      element = element.parentElement
    }

    return null
  }

  const selectRow = (id: number): void => {
    updateMemoryBrowser(presetId, { selectedId: id, editorOpen: true, pageScrollTop: pageScroller()?.scrollTop ?? 0 })
    window.requestAnimationFrame(() => document.getElementById(`memory-${id}`)?.focus({ preventScroll: true }))
  }

  const backToList = (): void => {
    updateMemoryBrowser(presetId, { editorOpen: false })
    window.requestAnimationFrame(() => {
      const scroller = pageScroller()

      if (scroller) {
        scroller.scrollTop = browser.pageScrollTop ?? 0
      }

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
      const updated = await requestGateway<MemoryItem>('memory.update', {
        memory_id: selected.id,
        content: draft.content.trim(),
        expected_version: draft.baseVersion,
        system_preset_id: presetId
      })

      if (!isCurrent()) {
        return
      }

      completeMemoryDraft('drafts', draftKey, draft, updated)

      if (!isLive()) {
        return
      }

      setSavedHint(
        status === 'active' ? t.saved : english ? 'Saved. This memory is in use again.' : '已保存，这条记忆已重新生效。'
      )
      await load(true)
    } catch (error) {
      if (!isCurrent()) {
        return
      }

      let confirmed: MemoryItem | undefined

      try {
        const result = await requestGateway<MemoryListResponse>('memory.list', {
          kind: 'recall',
          status: 'active',
          system_preset_id: presetId,
          q: draft.content.trim(),
          limit: PAGE_SIZE
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
            setSavedHint(
              status === 'active'
                ? t.saved
                : english
                  ? 'Saved. This memory is in use again.'
                  : '已保存，这条记忆已重新生效。'
            )
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
        title: english ? 'Forget this memory?' : '忘记这条记忆？',
        description: selected.content ?? '',
        confirmLabel: english ? 'Forget' : '忘记',
        variant: 'destructive'
      })) ||
      !isLive()
    ) {
      return
    }

    setBusy(true)

    try {
      await requestGateway('memory.delete', { memory_id: selected.id, system_preset_id: presetId })

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
    <div className="@container space-y-3" ref={contentRef}>
      <label className="block">
        <span className="sr-only">{english ? 'Search memories' : '搜索记忆'}</span>
        <input
          className={INPUT_CLASS}
          onChange={event => {
            setSavedHint(null)
            updateMemoryBrowser(presetId, {
              query: event.target.value,
              page: 0,
              selectedId: null,
              editorOpen: false,
              scrollTop: 0
            })
          }}
          placeholder={english ? 'Search remembered things' : '搜索记住的事'}
          type="search"
          value={query}
        />
      </label>
      <CapsuleTabs
        ariaLabel={t.tabAriaLabel}
        onChange={next =>
          updateMemoryBrowser(presetId, { status: next, page: 0, selectedId: null, editorOpen: false, scrollTop: 0 })
        }
        options={[
          { label: t.tabActive(counts?.active ?? '…'), value: 'active' },
          { label: t.tabCandidate(counts?.candidate ?? '…'), value: 'candidate' },
          { label: t.tabInvalidated(counts?.invalidated ?? '…'), value: 'invalidated' },
          { label: t.tabExpired(counts?.expired ?? '…'), value: 'expired' }
        ]}
        size="sm"
        value={status}
      />
      <p className={HINT_TEXT}>{t.maintenanceHint}</p>
      {hint && (
        <div className="flex flex-wrap items-center gap-2">
          <p aria-live="polite" className="text-xs text-amber-300/90">
            {hint}
          </p>
          <button className={BTN_GHOST} onClick={() => void load()} type="button">
            {dict.common.retry}
          </button>
        </div>
      )}
      {savedHint && (
        <p aria-live="polite" className={HINT_TEXT}>
          {savedHint}
        </p>
      )}
      {loading ? (
        <p className={HINT_TEXT}>{t.loading}</p>
      ) : rows.length === 0 ? (
        !hint && (
          <p className={HINT_TEXT}>{query ? (english ? 'No matching memories.' : '没有找到匹配的记忆。') : t.empty}</p>
        )
      ) : (
        <div className="grid min-w-0 items-start gap-4 @min-[720px]:grid-cols-[minmax(220px,0.85fr)_minmax(0,1.4fr)]">
          <div className={cn('min-w-0 space-y-3', browser.editorOpen && 'hidden @min-[720px]:block')}>
            <div
              className="max-h-[65vh] space-y-2 overflow-y-auto"
              onScroll={event => updateMemoryBrowser(presetId, { scrollTop: event.currentTarget.scrollTop })}
              ref={listRef}
            >
              {rows.map(row => (
                <button
                  aria-current={selected?.id === row.id ? 'true' : undefined}
                  className={cn(
                    'liquid-glass-card w-full rounded-xl border p-3 text-left',
                    selected?.id === row.id ? 'border-accent' : 'border-transparent'
                  )}
                  data-memory-id={row.id}
                  key={row.id}
                  onClick={() => selectRow(row.id)}
                  type="button"
                >
                  <p className="line-clamp-3 whitespace-pre-wrap break-words text-sm leading-6 text-body">
                    {row.content}
                  </p>
                  <p className={cn(HINT_TEXT, 'mt-2')}>
                    {row.context?.replace(/^recall:/, '') || '—'} ·{' '}
                    {row.updated_at ? new Date(row.updated_at).toLocaleDateString(english ? 'en' : 'zh-CN') : '—'}
                  </p>
                  {memory.drafts[`${presetId}:${row.id}`] && (
                    <span className="text-xs text-accent">{english ? 'Unsaved changes' : '未保存修改'}</span>
                  )}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <button
                className={BTN_SUBTLE}
                disabled={page === 0}
                onClick={() =>
                  updateMemoryBrowser(presetId, { page: page - 1, selectedId: null, editorOpen: false, scrollTop: 0 })
                }
                type="button"
              >
                {english ? 'Previous' : '上一页'}
              </button>
              <span className={HINT_TEXT}>
                {page + 1} / {Math.max(1, Math.ceil(total / PAGE_SIZE))} ·{' '}
                {english ? `${total} memories` : `共 ${total} 条`}
              </span>
              <button
                className={BTN_SUBTLE}
                disabled={(page + 1) * PAGE_SIZE >= total}
                onClick={() =>
                  updateMemoryBrowser(presetId, { page: page + 1, selectedId: null, editorOpen: false, scrollTop: 0 })
                }
                type="button"
              >
                {english ? 'Next' : '下一页'}
              </button>
            </div>
          </div>
          {selected && (
            <div
              className={cn(
                'liquid-glass-card min-w-0 space-y-3 rounded-2xl p-4',
                !browser.editorOpen && 'hidden @min-[720px]:block'
              )}
            >
              <button className={cn(BTN_GHOST, '@min-[720px]:hidden')} onClick={backToList} type="button">
                ← {english ? 'Back' : '返回'}
              </button>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label className="text-sm font-medium text-strong" htmlFor={`memory-${selected.id}`}>
                  {english ? 'Memory content' : '记忆内容'}
                </label>
                {draft && <span className={HINT_TEXT}>{english ? 'Unsaved changes' : '未保存修改'}</span>}
              </div>
              <textarea
                className={cn(INPUT_CLASS, 'min-h-40 resize-y')}
                disabled={busy}
                id={`memory-${selected.id}`}
                onChange={event => {
                  setSavedHint(null)
                  setMemoryDraft('drafts', draftKey, editMemoryDraft(draft, event.target.value, selected))
                }}
                rows={7}
                value={draft?.content ?? selected.content ?? ''}
              />
              {status !== 'active' && (
                <p className={HINT_TEXT}>
                  {english ? 'Saving makes this memory available again.' : '保存后，这条记忆会重新生效。'}
                </p>
              )}
              {conflict && (
                <div className="space-y-2 rounded-xl border border-line-hairline p-3">
                  <p className="text-xs text-amber-300/90">
                    {english
                      ? 'This memory changed on another device. Latest content:'
                      : '这条记忆已在其他设备修改，最新内容：'}
                  </p>
                  <p className="whitespace-pre-wrap break-words text-sm text-body">{selected.content}</p>
                  <button
                    className={BTN_SUBTLE}
                    disabled={busy}
                    onClick={() => {
                      if (draft) {
                        setMemoryDraft('drafts', draftKey, {
                          ...draft,
                          baseContent: selected.content ?? '',
                          baseVersion: selected.content_version
                        })
                      }
                    }}
                    type="button"
                  >
                    {english ? 'Keep my edits after review' : '已核对，保留我的修改'}
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
                      ? english
                        ? 'Save changes'
                        : '保存修改'
                      : english
                        ? 'Save and remember again'
                        : '保存并重新记住'}
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
                  {english ? 'Forget this memory' : '忘记这条记忆'}
                </button>
              </div>
              <p className={HINT_TEXT}>
                {selected.context?.replace(/^recall:/, '') || '—'} · {t.updated} {selected.updated_at ?? '—'}
              </p>
              <MemoryDetails row={selected} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function MemoryDetails({ row }: { row: MemoryItem }): React.ReactElement {
  const t = useStrings().settings.memory
  const parsed = safeJsonParse<unknown>(row.tags, [])
  const tags = Array.isArray(parsed) ? parsed.map(String) : []

  return (
    <details className="space-y-2 border-t border-line-hairline pt-3 text-xs text-muted">
      <summary className="cursor-pointer">{t.evidence}</summary>
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
    </details>
  )
}
