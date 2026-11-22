import { useCallback, useEffect, useId, useRef, useState } from 'react'

import { MAX_USER_TEXT } from '@/features/character'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import { requestResource } from '@/shared/lib/resource-request'
import { cn } from '@/shared/lib/utils'
import { notifyError } from '@/shared/store/notifications'
import { getStrings, useStrings } from '@/shared/strings'
import { BTN_SUBTLE, confirmAction, DatePicker, FIELD_LABEL, HINT_TEXT, INPUT_CLASS } from '@/ui'
import type { CompanionMemoryDraft } from '@client-shared/ipc/ui-state'
import type { MemoryItem, MemoryListResponse, MemoryProfileChange, UserProfileField } from '@protocol'

import { editMemoryDraft } from './companion-editor-state'
import { useMemoryChanged } from './use-memory-changed'

export const PROFILE_FIELDS = [
  'user_call_name',
  'user_gender',
  'user_birthday',
  'user_hobbies',
  'user_freeform'
] as const satisfies readonly UserProfileField[]

export type ProfileDrafts = Partial<Record<UserProfileField, CompanionMemoryDraft>>

export function profileChanges(drafts: ProfileDrafts): MemoryProfileChange[] {
  return PROFILE_FIELDS.flatMap(field => {
    const draft = drafts[field]

    if (!draft || draft.content.trim() === draft.baseContent.trim()) {
      return []
    }

    return [{ field, value: draft.content.trim() || null, expected_version: draft.baseVersion }]
  })
}

export function conflictingProfileFields(rows: MemoryItem[] | null, drafts: ProfileDrafts): UserProfileField[] {
  if (!rows) {
    return []
  }

  return profileChanges(drafts)
    .filter(
      change => (rows.find(row => row.slot_key === change.field)?.content_version ?? 0) !== change.expected_version
    )
    .map(change => change.field)
}

export function profileChangesMatch(
  rows: MemoryItem[],
  changes: Pick<MemoryProfileChange, 'field' | 'value'>[]
): boolean {
  return changes.every(
    change => (rows.find(row => row.slot_key === change.field)?.content ?? '') === (change.value ?? '')
  )
}

export async function confirmProfileChanges(changes: MemoryProfileChange[]): Promise<boolean> {
  const cleared = changes.filter(change => !change.value && change.expected_version > 0)

  if (!cleared.length) {
    return true
  }

  const dict = getStrings()

  return confirmAction({
    title: dict.settings.persona.profileForgetTitle,
    description: cleared.map(change => dict.settings.memory.profile.fields[change.field]).join('、'),
    confirmLabel: dict.settings.persona.profileForgetAndSave,
    variant: 'destructive'
  })
}

export function useUserProfileRows() {
  const { loadFailedHint, loadFailedToast } = useStrings().settings.memory
  const [rows, setRows] = useState<MemoryItem[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const begin = useAsyncGuard()
  const requestId = useRef(0)

  const load = useCallback(
    async (background = false): Promise<MemoryItem[] | null> => {
      const mounted = begin()
      const generation = ++requestId.current
      const isLive = (): boolean => mounted() && generation === requestId.current

      if (!background) {
        setLoading(true)
      }

      try {
        const all: MemoryItem[] = []
        let cursor: string | null = null

        do {
          const result: MemoryListResponse = await requestResource('resource_catalog.get_memories', {
            query: { kind: 'user_profile', status: 'active', system_preset_id: 'companion', cursor, limit: 100 }
          })

          if (!isLive() || result.system_preset_id !== 'companion') {
            return null
          }

          all.push(...result.memories)
          cursor = result.next_cursor ?? null

          if (!result.memories.length) {
            break
          }
        } while (cursor)

        setRows(all)
        setLoadError(null)

        return all
      } catch (error) {
        if (isLive()) {
          setLoadError(loadFailedHint)
          notifyError(error, loadFailedToast)
        }

        return null
      } finally {
        if (isLive()) {
          setLoading(false)
        }
      }
    },
    [begin, loadFailedHint, loadFailedToast]
  )

  useEffect(() => {
    void load()

    return () => {
      requestId.current += 1
    }
  }, [load])

  const refresh = useCallback(async () => {
    await load(true)
  }, [load])

  useMemoryChanged('companion', refresh)

  return { rows, setRows, loading, loadError, load }
}

export function UserProfileForm({
  busy,
  rows,
  drafts,
  onEdit
}: {
  busy: boolean
  rows: MemoryItem[]
  drafts: ProfileDrafts
  onEdit: (key: UserProfileField, draft: CompanionMemoryDraft | undefined) => void
}): React.JSX.Element {
  const dict = useStrings()
  const labels = dict.settings.memory.profile.fields
  const id = useId()
  const [choosingBirthday, setChoosingBirthday] = useState(false)
  const conflicts = conflictingProfileFields(rows, drafts)

  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        {PROFILE_FIELDS.map(key => {
          const row = rows.find(item => item.slot_key === key)
          const value = drafts[key]?.content ?? row?.content ?? ''
          const inputId = id + key
          const multiline = key === 'user_hobbies' || key === 'user_freeform'

          const onChange = (next: string): void => {
            onEdit(key, editMemoryDraft(drafts[key], next, row))

            if (key === 'user_birthday' && !next) {
              setChoosingBirthday(false)
            }
          }

          return (
            <div className={cn('block', multiline && 'sm:col-span-2')} key={key}>
              <label className={FIELD_LABEL} htmlFor={inputId}>
                {labels[key]}
              </label>
              {key === 'user_birthday' ? (
                value || choosingBirthday ? (
                  <DatePicker
                    clearLabel={dict.common.clear}
                    disabled={busy}
                    id={inputId}
                    onChange={onChange}
                    placeholder={dict.common.pickDate}
                    value={value}
                  />
                ) : (
                  <button
                    className={cn(INPUT_CLASS, 'text-left')}
                    disabled={busy}
                    id={inputId}
                    onClick={() => setChoosingBirthday(true)}
                    type="button"
                  >
                    {dict.common.pickDate}
                  </button>
                )
              ) : multiline ? (
                <textarea
                  className={INPUT_CLASS}
                  disabled={busy}
                  id={inputId}
                  maxLength={MAX_USER_TEXT}
                  onChange={event => onChange(event.target.value)}
                  rows={3}
                  value={value}
                />
              ) : (
                <input
                  className={INPUT_CLASS}
                  disabled={busy}
                  id={inputId}
                  maxLength={MAX_USER_TEXT}
                  onChange={event => onChange(event.target.value)}
                  value={value}
                />
              )}
            </div>
          )
        })}
      </div>
      {!busy && conflicts.length > 0 && (
        <div className="mt-3 space-y-2 rounded-xl border border-line-hairline p-3" role="alert">
          <p className="text-[11px] text-warning">{dict.settings.persona.profileConflictHint}</p>
          <dl className="space-y-1">
            {conflicts.map(key => (
              <div key={key}>
                <dt className={HINT_TEXT}>{labels[key]}</dt>
                <dd className="whitespace-pre-wrap break-words text-[13px] text-body">
                  {rows.find(row => row.slot_key === key)?.content || '—'}
                </dd>
              </div>
            ))}
          </dl>
          <button
            className={BTN_SUBTLE}
            disabled={busy}
            onClick={() => {
              for (const key of conflicts) {
                const row = rows.find(item => item.slot_key === key)
                const draft = drafts[key]

                if (draft) {
                  onEdit(key, editMemoryDraft(undefined, draft.content, row))
                }
              }
            }}
            type="button"
          >
            {dict.common.keepMyEdits}
          </button>
        </div>
      )}
    </>
  )
}
