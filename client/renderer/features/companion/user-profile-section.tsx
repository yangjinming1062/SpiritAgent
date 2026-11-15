import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { MAX_USER_TEXT } from '@/features/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { requestResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import { gatewayApi } from '@/shared/platform/desktop'
import { $gateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import {
  BTN_GHOST,
  BTN_SUBTLE,
  confirmAction,
  CreationActionBar,
  CreationFeedback,
  DatePicker,
  ErrorBanner,
  FIELD_LABEL,
  HINT_TEXT,
  INPUT_CLASS,
  OverflowMenu,
  OverflowMenuAction,
  SECTION_TITLE
} from '@/ui'
import type { CompanionMemoryDraft } from '@client-shared/ipc/ui-state'
import { isRecord } from '@client-shared/is-record'
import type {
  MemoryItem,
  MemoryListResponse,
  MemoryProfileChange,
  MemoryProfileUpdateRequest,
  UserProfileField
} from '@protocol'

import { $companionEditors, completeMemoryDraft, editMemoryDraft, setMemoryDraft } from './companion-editor-state'

export function useMemoryChanged(presetId: string, refresh: () => Promise<void>): void {
  const gateway = useStore($gateway)
  const beginGuard = useAsyncGuard()
  useEffect(() => {
    if (!gateway) {
      return
    }

    const isLive = beginGuard()

    const onEvent = (event: GatewayEvent): void => {
      if (
        isLive() &&
        event.type === 'memory.changed' &&
        isRecord(event.payload) &&
        event.payload.system_preset_id === presetId
      ) {
        void refresh()
      }
    }

    return gatewayApi().onGatewayEvent(({ event }) => onEvent(event))
  }, [beginGuard, gateway, presetId, refresh])
}

interface ProfileField {
  key: UserProfileField
  multiline?: boolean
  date?: boolean
}

const PROFILE_FIELDS: readonly ProfileField[] = [
  { key: 'user_call_name' },
  { key: 'user_gender' },
  { key: 'user_birthday', date: true },
  { key: 'user_hobbies', multiline: true },
  { key: 'user_freeform', multiline: true }
]

export function UserProfileSection(): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.memory
  const p = t.profile
  const drafts = useStore($companionEditors).memory.profileDrafts
  const [rows, setRows] = useState<MemoryItem[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [hint, setHint] = useState<string | null>(null)
  const [savedHint, setSavedHint] = useState<string | null>(null)
  const [editingExtra, setEditingExtra] = useState<string | null>(null)
  const begin = useAsyncGuard()
  const requestId = useRef(0)

  const load = useCallback(
    async (background = false): Promise<MemoryItem[] | null> => {
      const isLive = begin()
      const generation = ++requestId.current

      if (!background) {
        setLoading(true)
      }

      setHint(null)

      try {
        const all: MemoryItem[] = []
        let cursor: string | null = null

        do {
          const result: MemoryListResponse = await requestResource('resource_catalog.get_memories', {
            query: {
              kind: 'user_profile',
              status: 'active',
              system_preset_id: 'companion',
              cursor,
              limit: 100
            }
          })

          if (!isLive() || generation !== requestId.current || result.system_preset_id !== 'companion') {
            return null
          }

          all.push(...result.memories)
          cursor = result.next_cursor ?? null

          if (!result.memories.length) {
            break
          }
        } while (cursor)

        setRows(all)

        return all
      } catch (error) {
        if (isLive() && generation === requestId.current) {
          setHint(t.loadFailedHint)
          notifyError(error, t.loadFailedToast)
        }

        return null
      } finally {
        if (isLive() && generation === requestId.current) {
          setLoading(false)
        }
      }
    },
    [begin, t.loadFailedHint, t.loadFailedToast]
  )

  useEffect(() => {
    void load()

    return () => {
      requestId.current += 1
    }
  }, [load])

  const refresh = useCallback(async (): Promise<void> => {
    await load(true)
  }, [load])

  useMemoryChanged('companion', refresh)
  const rowOf = (field: ProfileField): MemoryItem | undefined => rows?.find(row => row.slot_key === field.key)
  const changed = PROFILE_FIELDS.filter(field => !!drafts[field.key])
  const conflicts = changed.filter(field => drafts[field.key]?.baseVersion !== (rowOf(field)?.content_version ?? 0))
  const extras = rows?.filter(row => !PROFILE_FIELDS.some(field => field.key === row.slot_key)) ?? []

  const saveProfile = async (): Promise<void> => {
    const isLive = begin()
    const isCurrent = captureAuthScope()

    if (!isCurrent) {
      return
    }

    const changes: MemoryProfileChange[] = changed.map(field => ({
      field: field.key,
      value: drafts[field.key].content.trim() || null,
      expected_version: drafts[field.key].baseVersion
    }))

    const cleared = changed.filter(field => !drafts[field.key].content.trim() && drafts[field.key].baseVersion !== 0)

    if (
      cleared.length &&
      !(await confirmAction({
        title: dict.settings.persona.profileForgetTitle,
        description: cleared.map(field => p.fields[field.key]).join('、'),
        confirmLabel: dict.settings.persona.profileForgetAndSave,
        variant: 'destructive'
      }))
    ) {
      return
    }

    if (!isLive()) {
      return
    }

    setBusy(true)
    setHint(null)
    setSavedHint(null)

    try {
      const result = await requestResource('resource_catalog.patch_memory_profile', {
        body: {
          system_preset_id: 'companion',
          changes
        } satisfies MemoryProfileUpdateRequest
      })

      if (!isCurrent()) {
        return
      }

      for (const field of changed) {
        completeMemoryDraft(
          'profileDrafts',
          field.key,
          drafts[field.key],
          result.memories.find(row => row.slot_key === field.key)
        )
      }

      if (!isLive()) {
        return
      }

      setRows(result.memories)
      setSavedHint(t.saved)
    } catch (error) {
      if (isLive()) {
        const latest = await load(true)

        if (!isLive()) {
          return
        }

        const confirmed =
          !!latest &&
          changed.every(
            field =>
              (latest.find(row => row.slot_key === field.key)?.content ?? '') === drafts[field.key].content.trim()
          )

        if (confirmed && latest) {
          for (const field of changed) {
            completeMemoryDraft(
              'profileDrafts',
              field.key,
              drafts[field.key],
              latest.find(row => row.slot_key === field.key)
            )
          }

          setSavedHint(t.saved)
        } else {
          setHint(t.saveFailedHint)
          notifyError(error, t.saveFailedToast)
        }
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  const saveExtra = async (row: MemoryItem): Promise<void> => {
    const key = `extra:${row.id}`
    const draft = drafts[key]

    if (!draft) {
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
        path: { memory_id: row.id },
        body: {
          content: draft.content.trim(),
          expected_version: draft.baseVersion,
          system_preset_id: 'companion'
        }
      })

      if (!isCurrent()) {
        return
      }

      completeMemoryDraft('profileDrafts', key, draft, updated)

      if (!isLive()) {
        return
      }

      setRows(
        previous =>
          previous?.map(entry =>
            entry.id === updated.id && entry.content_version <= updated.content_version ? updated : entry
          ) ?? null
      )
      setSavedHint(t.saved)
      setEditingExtra(null)
    } catch (error) {
      if (isLive()) {
        const latest = await load(true)

        if (!isLive()) {
          return
        }

        const confirmed = latest?.find(
          entry =>
            entry.id === row.id && entry.content === draft.content.trim() && entry.content_version > draft.baseVersion
        )

        if (confirmed) {
          completeMemoryDraft('profileDrafts', key, draft, confirmed)
          setSavedHint(t.saved)
        } else {
          setHint(t.saveFailedHint)
          notifyError(error, t.saveFailedToast)
        }
      }
    } finally {
      if (isLive()) {
        setBusy(false)
      }
    }
  }

  const forgetExtra = async (row: MemoryItem): Promise<void> => {
    const isLive = begin()
    const isCurrent = captureAuthScope()

    if (!isCurrent) {
      return
    }

    if (
      !(await confirmAction({
        title: dict.settings.persona.profileForgetOneTitle,
        description: row.content ?? '',
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
        path: { memory_id: row.id },
        query: { system_preset_id: 'companion' }
      })

      if (!isCurrent()) {
        return
      }

      setMemoryDraft('profileDrafts', `extra:${row.id}`, undefined)

      if (!isLive()) {
        return
      }

      setEditingExtra(null)
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
      <div className="flex shrink-0 flex-wrap items-baseline gap-x-2 gap-y-1">
        <p className={SECTION_TITLE}>{p.title}</p>
        {changed.length > 0 ? <span className={HINT_TEXT}>{dict.common.unsavedChanges}</span> : null}
        {savedHint ? (
          <span aria-live="polite" className={HINT_TEXT}>
            {savedHint}
          </span>
        ) : null}
      </div>
      {hint ? <ErrorBanner message={hint} onRetry={() => void load()} retryLabel={dict.common.retry} /> : null}
      {loading ? (
        <p className={HINT_TEXT}>{t.loading}</p>
      ) : !rows ? null : (
        <>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain pb-1 pr-1">
            <div className="liquid-glass-card rounded-2xl p-4">
              <p className={cn(HINT_TEXT, 'mb-3 max-w-xl')}>{p.hint}</p>
              <div className="grid gap-3 sm:grid-cols-2">
                {PROFILE_FIELDS.map(field => {
                  const row = rowOf(field)
                  const value = drafts[field.key]?.content ?? row?.content ?? ''

                  const onChange = (next: string): void => {
                    setSavedHint(null)
                    setMemoryDraft('profileDrafts', field.key, editMemoryDraft(drafts[field.key], next, row))
                  }

                  return (
                    <div className={cn('block', field.multiline && 'sm:col-span-2')} key={field.key}>
                      <label className={FIELD_LABEL} htmlFor={`profile-${field.key}`}>
                        {p.fields[field.key]}
                      </label>
                      {field.date ? (
                        <DatePicker
                          clearLabel={dict.common.clear}
                          disabled={busy}
                          id={`profile-${field.key}`}
                          onChange={onChange}
                          placeholder={dict.common.pickDate}
                          value={value}
                        />
                      ) : field.multiline ? (
                        <textarea
                          className={INPUT_CLASS}
                          disabled={busy}
                          id={`profile-${field.key}`}
                          maxLength={MAX_USER_TEXT}
                          onChange={event => onChange(event.target.value)}
                          rows={3}
                          value={value}
                        />
                      ) : (
                        <input
                          className={INPUT_CLASS}
                          disabled={busy}
                          id={`profile-${field.key}`}
                          maxLength={MAX_USER_TEXT}
                          onChange={event => onChange(event.target.value)}
                          value={value}
                        />
                      )}
                    </div>
                  )
                })}
              </div>
              {conflicts.length > 0 ? (
                <div className="mt-3 space-y-2 rounded-xl border border-line-hairline p-3">
                  <p className="text-[11px] text-warning">{dict.settings.persona.profileConflictHint}</p>
                  <dl className="space-y-1">
                    {conflicts.map(field => (
                      <div key={field.key}>
                        <dt className={HINT_TEXT}>{p.fields[field.key]}</dt>
                        <dd className="whitespace-pre-wrap break-words text-[13px] text-body">
                          {rowOf(field)?.content || '—'}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <button
                    className={BTN_SUBTLE}
                    disabled={busy}
                    onClick={() => {
                      for (const field of conflicts) {
                        setMemoryDraft('profileDrafts', field.key, {
                          ...drafts[field.key],
                          baseContent: rowOf(field)?.content ?? '',
                          baseVersion: rowOf(field)?.content_version ?? 0
                        })
                      }
                    }}
                    type="button"
                  >
                    {dict.common.keepMyEdits}
                  </button>
                </div>
              ) : null}
            </div>
            {extras.map(row => {
              const key = `extra:${row.id}`
              const draft = drafts[key]
              const editing = editingExtra === row.id

              return (
                <ExtraCard
                  busy={busy}
                  draft={draft}
                  editing={editing}
                  key={row.id}
                  onCancel={() => setEditingExtra(null)}
                  onChange={value => {
                    setSavedHint(null)
                    setMemoryDraft('profileDrafts', key, editMemoryDraft(draft, value, row))
                  }}
                  onForget={() => void forgetExtra(row)}
                  onKeepMine={() => {
                    if (draft) {
                      setMemoryDraft('profileDrafts', key, {
                        ...draft,
                        baseContent: row.content ?? '',
                        baseVersion: row.content_version
                      })
                    }
                  }}
                  onSave={() => void saveExtra(row)}
                  onSelect={() => setEditingExtra(row.id)}
                  reset={() => setMemoryDraft('profileDrafts', key, undefined)}
                  row={row}
                />
              )
            })}
          </div>
          {changed.length > 0 || conflicts.length > 0 ? (
            <CreationActionBar
              description={
                conflicts.length > 0 ? (
                  <CreationFeedback state="error" title={dict.settings.persona.profileConflictHint} />
                ) : (
                  <span className={HINT_TEXT}>{dict.common.unsavedChanges}</span>
                )
              }
            >
              <button
                className={BTN_SUBTLE}
                disabled={busy || !changed.length || conflicts.length > 0}
                onClick={() => void saveProfile()}
                type="button"
              >
                {busy ? dict.common.saving : dict.common.saveChanges}
              </button>
              <button
                className={BTN_GHOST}
                disabled={busy || !changed.length}
                onClick={() => {
                  for (const field of PROFILE_FIELDS) {
                    setMemoryDraft('profileDrafts', field.key, undefined)
                  }
                }}
                type="button"
              >
                {dict.common.cancel}
              </button>
            </CreationActionBar>
          ) : null}
        </>
      )}
    </div>
  )
}

function ExtraCard({
  busy,
  draft,
  editing,
  onCancel,
  onChange,
  onForget,
  onKeepMine,
  onSave,
  onSelect,
  reset,
  row
}: {
  busy: boolean
  draft?: CompanionMemoryDraft
  editing: boolean
  onCancel: () => void
  onChange: (value: string) => void
  onForget: () => void
  onKeepMine: () => void
  onSave: () => void
  onSelect: () => void
  reset: () => void
  row: MemoryItem
}): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.memory
  const p = dict.settings.persona
  const conflict = Boolean(draft && draft.baseVersion !== row.content_version)

  if (!editing) {
    return (
      <article className="liquid-glass-card rounded-xl">
        <div className="flex items-start gap-1 py-1 pl-3 pr-1.5">
          <button
            aria-expanded={false}
            className="min-w-0 flex-1 rounded-lg py-1.5 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
            onClick={onSelect}
            type="button"
          >
            <p className="text-[11px] text-muted">{row.context || p.profileAdditionalDetail}</p>
            <p className="mt-0.5 line-clamp-2 whitespace-pre-wrap break-words text-[13px] leading-6 text-body">
              {row.content}
            </p>
            {draft ? <span className="text-[10px] text-accent">{dict.common.unsavedChanges}</span> : null}
          </button>
          <OverflowMenu label={dict.common.moreActions}>
            <OverflowMenuAction label={dict.settings.persona.memoryEdit} onClick={onSelect} />
            <OverflowMenuAction danger label={p.profileForgetOneAction} onClick={onForget} />
          </OverflowMenu>
        </div>
      </article>
    )
  }

  return (
    <article className="liquid-glass-card rounded-xl outline-1 -outline-offset-1 outline-accent">
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[13px] font-medium text-strong">{row.context || p.profileAdditionalDetail}</p>
          <div className="flex items-center gap-2">
            {draft ? <span className={HINT_TEXT}>{dict.common.unsavedChanges}</span> : null}
            <button className={BTN_GHOST} onClick={onCancel} type="button">
              {p.collapseAction}
            </button>
          </div>
        </div>
        <textarea
          className={INPUT_CLASS}
          disabled={busy}
          onChange={event => onChange(event.target.value)}
          rows={3}
          value={draft?.content ?? row.content ?? ''}
        />
        {conflict ? (
          <div className="space-y-2 rounded-xl border border-line-hairline p-3">
            <p className="text-[11px] text-warning">{p.profileLatestContent}</p>
            <p className="whitespace-pre-wrap break-words text-[13px] text-body">{row.content}</p>
            <button className={BTN_SUBTLE} disabled={busy} onClick={onKeepMine} type="button">
              {dict.common.keepMyEdits}
            </button>
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <button
            className={BTN_SUBTLE}
            disabled={busy || !draft?.content.trim() || conflict}
            onClick={onSave}
            type="button"
          >
            {busy ? dict.common.saving : dict.common.saveChanges}
          </button>
          <button className={BTN_GHOST} disabled={busy || !draft} onClick={reset} type="button">
            {dict.common.cancel}
          </button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line-hairline pt-2">
          <p className={cn(HINT_TEXT, 'min-w-0 break-words')}>
            {t.updated} {row.updated_at ?? '—'}
          </p>
          <button className={cn(BTN_GHOST, 'text-danger-fg')} disabled={busy} onClick={onForget} type="button">
            {p.profileForgetOneAction}
          </button>
        </div>
      </div>
    </article>
  )
}
