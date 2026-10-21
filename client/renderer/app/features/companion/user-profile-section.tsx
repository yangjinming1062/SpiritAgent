import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { MAX_USER_TEXT } from '@/modules/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { captureAuthScope } from '@/shared/lib/authed-api'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { isRecord } from '@/shared/lib/is-record'
import { requestResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import {
  BTN_GHOST,
  BTN_SUBTLE,
  confirmAction,
  DatePicker,
  ErrorBanner,
  FIELD_LABEL,
  HINT_TEXT,
  INPUT_CLASS,
  SECTION_TITLE
} from '@/shared/panel'
import { gatewayApi } from '@/shared/platform/desktop'
import { $gateway } from '@/shared/store/gateway'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'
import type { MemoryItem, MemoryProfileChange, MemoryProfileUpdateRequest, UserProfileField } from '@protocol'

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
  context: string
  key: UserProfileField
  multiline?: boolean
  date?: boolean
}

const PROFILE_FIELDS: readonly ProfileField[] = [
  { context: 'user_profile:preferred_name', key: 'user_call_name' },
  { context: 'user_profile:gender', key: 'user_gender' },
  { context: 'user_profile:birthday', key: 'user_birthday', date: true },
  { context: 'user_profile:hobbies', key: 'user_hobbies', multiline: true },
  { context: 'user_profile:freeform', key: 'user_freeform', multiline: true }
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
        let total = 0

        do {
          const result = await requestResource('resource_catalog.get_memories', {
            query: {
              kind: 'user_profile',
              status: 'active',
              system_preset_id: 'companion',
              offset: all.length,
              limit: 100
            }
          })

          if (!isLive() || generation !== requestId.current || result.system_preset_id !== 'companion') {
            return null
          }

          all.push(...result.memories)
          total = result.total

          if (!result.memories.length) {
            break
          }
        } while (all.length < total)

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
  const rowOf = (field: ProfileField): MemoryItem | undefined => rows?.find(row => row.context === field.context)
  const changed = PROFILE_FIELDS.filter(field => !!drafts[field.key])
  const conflicts = changed.filter(field => drafts[field.key]?.baseVersion !== (rowOf(field)?.content_version ?? 0))

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
          result.memories.find(row => row.context === field.context)
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
              (latest.find(row => row.context === field.context)?.content ?? '') === drafts[field.key].content.trim()
          )

        if (confirmed && latest) {
          for (const field of changed) {
            completeMemoryDraft(
              'profileDrafts',
              field.key,
              drafts[field.key],
              latest.find(row => row.context === field.context)
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
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className={SECTION_TITLE}>{p.title}</p>
        {changed.length > 0 && <span className={HINT_TEXT}>{dict.common.unsavedChanges}</span>}
      </div>
      <p className={HINT_TEXT}>{p.hint}</p>
      {hint && <ErrorBanner message={hint} onRetry={() => void load()} retryLabel={dict.common.retry} />}
      {savedHint && (
        <p aria-live="polite" className={HINT_TEXT}>
          {savedHint}
        </p>
      )}
      {loading ? (
        <p className={HINT_TEXT}>{t.loading}</p>
      ) : !rows ? null : (
        <>
          <div className="liquid-glass-card space-y-4 rounded-2xl p-4">
            {PROFILE_FIELDS.map(field => {
              const row = rowOf(field)
              const value = drafts[field.key]?.content ?? row?.content ?? ''

              const onChange = (next: string): void => {
                setSavedHint(null)
                setMemoryDraft('profileDrafts', field.key, editMemoryDraft(drafts[field.key], next, row))
              }

              return (
                <div className="block" key={field.key}>
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
                      rows={2}
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
            {conflicts.length > 0 && (
              <div className="space-y-2 rounded-xl border border-line-hairline p-3">
                <p className="text-xs text-amber-300/90">{dict.settings.persona.profileConflictHint}</p>
                <dl className="space-y-1">
                  {conflicts.map(field => (
                    <div key={field.key}>
                      <dt className={HINT_TEXT}>{p.fields[field.key]}</dt>
                      <dd className="whitespace-pre-wrap break-words text-sm text-body">
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
            )}
            <div className="flex flex-wrap items-center gap-2">
              <button
                className={BTN_SUBTLE}
                disabled={busy || !changed.length || !!conflicts.length}
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
            </div>
          </div>
          {rows
            .filter(row => !PROFILE_FIELDS.some(field => field.context === row.context))
            .map(row => {
              const key = `extra:${row.id}`
              const draft = drafts[key]
              const conflict = !!draft && draft.baseVersion !== row.content_version

              return (
                <div className="liquid-glass-card space-y-3 rounded-2xl p-4" key={row.id}>
                  <label className="block">
                    <span className={FIELD_LABEL}>
                      {row.context?.replace(/^user_profile:/, '') || dict.settings.persona.profileAdditionalDetail}
                    </span>
                    <textarea
                      className={INPUT_CLASS}
                      disabled={busy}
                      onChange={event => {
                        setSavedHint(null)
                        setMemoryDraft('profileDrafts', key, editMemoryDraft(draft, event.target.value, row))
                      }}
                      rows={2}
                      value={draft?.content ?? row.content ?? ''}
                    />
                  </label>
                  {draft && <p className={HINT_TEXT}>{dict.common.unsavedChanges}</p>}
                  {conflict && (
                    <div className="space-y-2">
                      <p className="text-xs text-amber-300/90">{dict.settings.persona.profileLatestContent}</p>
                      <p className="whitespace-pre-wrap break-words text-sm text-body">{row.content}</p>
                      <button
                        className={BTN_SUBTLE}
                        disabled={busy}
                        onClick={() => {
                          if (draft) {
                            setMemoryDraft('profileDrafts', key, {
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
                  <div className="flex flex-wrap gap-2">
                    <button
                      className={BTN_SUBTLE}
                      disabled={busy || !draft?.content.trim() || conflict}
                      onClick={() => void saveExtra(row)}
                      type="button"
                    >
                      {dict.common.saveChanges}
                    </button>
                    <button
                      className={BTN_GHOST}
                      disabled={busy || !draft}
                      onClick={() => setMemoryDraft('profileDrafts', key, undefined)}
                      type="button"
                    >
                      {dict.common.cancel}
                    </button>
                    <button
                      className={cn(BTN_GHOST, 'text-danger-fg')}
                      disabled={busy}
                      onClick={() => void forgetExtra(row)}
                      type="button"
                    >
                      {dict.settings.persona.profileForgetOneAction}
                    </button>
                  </div>
                </div>
              )
            })}
        </>
      )}
    </section>
  )
}
