import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { MAX_USER_TEXT } from '@/modules/character'
import { requestGateway } from '@/shared'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_SUBTLE, DatePicker, HINT_TEXT, INPUT_CLASS, SECTION_TITLE } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

type ProfileFieldKey = 'user_call_name' | 'user_gender' | 'user_birthday' | 'user_hobbies' | 'user_freeform'

interface ProfileField {
  context: string
  key: ProfileFieldKey
  multiline?: boolean
  date?: boolean
}

interface ProfileMemoryRow {
  id: number
  context: string | null
  content: string | null
}

interface ProfileListResponse {
  system_preset_id: string
  memories: ProfileMemoryRow[]
  counts: { user_profile: number }
}

interface ProfileEntryEditorProps {
  busy: boolean
  date?: boolean
  dirty: boolean
  inputId: string
  label: string
  maxLength?: number
  multiline?: boolean
  onChange: (value: string) => void
  onDelete?: () => void
  onSave: () => void
  persisted: boolean
  value: string
}

const PROFILE_PRESET_ID = 'companion'
// 资料 context 形如 user_profile:< 键 >（后端 memory_namespaces.py）；自定义条目只展示键名。
const USER_PROFILE_CONTEXT_PREFIX = /^user_profile:/

// 标签与后端资料槽位一一对应；未知标签保留为可编辑的自定义条目。
const PROFILE_FIELDS: readonly ProfileField[] = [
  { context: 'user_profile:preferred_name', key: 'user_call_name' },
  { context: 'user_profile:gender', key: 'user_gender' },
  { context: 'user_profile:birthday', key: 'user_birthday', date: true },
  { context: 'user_profile:hobbies', key: 'user_hobbies', multiline: true },
  { context: 'user_profile:freeform', key: 'user_freeform', multiline: true }
]

// 草稿 trim 后非空且不同于已存内容才需要保存，返回待提交的值。
function pendingValue(draft: string, saved: string | null | undefined): string | null {
  const value = draft.trim()

  return value && value !== (saved ?? '').trim() ? value : null
}

export function UserProfileSection({
  onCount
}: {
  onCount: React.Dispatch<React.SetStateAction<number | null>>
}): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.memory
  const p = t.profile

  const [rows, setRows] = useState<ProfileMemoryRow[]>([])
  const [loading, setLoading] = useState(true)
  const [hint, setHint] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busyKeys, setBusyKeys] = useState<Record<string, boolean>>({})
  const loadIdRef = useRef(0)
  const mountedRef = useRef(true)

  // background 静默刷新用于写后对账，不把表单切成 loading 占位。
  const load = useCallback(
    async (background = false): Promise<void> => {
      if (!mountedRef.current) {
        return
      }

      const id = ++loadIdRef.current

      if (!background) {
        setLoading(true)
      }

      setHint(null)

      try {
        const res = await requestGateway<ProfileListResponse>('memory.list', {
          kind: 'user_profile',
          status: 'active',
          system_preset_id: PROFILE_PRESET_ID
        })

        if (!mountedRef.current || loadIdRef.current !== id || res.system_preset_id !== PROFILE_PRESET_ID) {
          return
        }

        setRows(res.memories)
        onCount(res.counts.user_profile)
      } catch (err) {
        if (!mountedRef.current || loadIdRef.current !== id) {
          return
        }

        setHint(t.loadFailedHint)
        notifyError(err, t.loadFailedToast)
      } finally {
        if (mountedRef.current && loadIdRef.current === id) {
          setLoading(false)
        }
      }
    },
    [onCount, t.loadFailedHint, t.loadFailedToast]
  )

  useEffect(() => {
    mountedRef.current = true
    void load()

    return () => {
      mountedRef.current = false
      loadIdRef.current += 1
    }
  }, [load])

  const setBusy = (key: string, busy: boolean): void => {
    setBusyKeys(previous => ({ ...previous, [key]: busy }))
  }

  // 三个写操作共用的失败与收尾：卸载后不再回写，失败提示由调用方给出。
  const guarded = async (
    key: string,
    failure: { hint: string; toast: string },
    action: () => Promise<void>
  ): Promise<void> => {
    setBusy(key, true)

    try {
      await action()
    } catch (err) {
      if (mountedRef.current) {
        setHint(failure.hint)
        notifyError(err, failure.toast)
      }
    } finally {
      if (mountedRef.current) {
        setBusy(key, false)
      }
    }
  }

  // 资料槽位的 context 在后端唯一（uq_memories_user_context），按 context 取首条即可。
  const rowOf = (field: ProfileField): ProfileMemoryRow | undefined => rows.find(row => row.context === field.context)

  const saveKnown = async (field: ProfileField): Promise<void> => {
    const row = rowOf(field)
    const value = pendingValue(drafts[field.key] ?? row?.content ?? '', row?.content)

    if (!value) {
      return
    }

    await guarded(field.key, { hint: t.saveFailedHint, toast: t.saveFailedToast }, async () => {
      await requestGateway('onboarding.submit', { field: field.key, value })

      if (!mountedRef.current) {
        return
      }

      setDrafts(previous => ({ ...previous, [field.key]: value }))

      if (row) {
        setRows(previous =>
          previous.map(entry => (entry.context === field.context ? { ...entry, content: value } : entry))
        )
      } else {
        onCount(previous => (previous === null ? previous : previous + 1))
      }

      await load(true)
    })
  }

  const saveExtra = async (row: ProfileMemoryRow): Promise<void> => {
    const key = row.context ?? String(row.id)
    const value = pendingValue(drafts[key] ?? row.content ?? '', row.content)

    if (!value) {
      return
    }

    await guarded(key, { hint: t.saveFailedHint, toast: t.saveFailedToast }, async () => {
      const updated = await requestGateway<ProfileMemoryRow>('memory.update', {
        memory_id: row.id,
        content: value,
        system_preset_id: PROFILE_PRESET_ID
      })

      if (!mountedRef.current) {
        return
      }

      setDrafts(previous => ({ ...previous, [key]: updated.content ?? value }))
      setRows(previous => previous.map(entry => (entry.id === row.id ? updated : entry)))
      await load(true)
    })
  }

  const remove = async (key: string, memoryId: number): Promise<void> => {
    await guarded(key, { hint: t.deleteFailedHint, toast: t.deleteFailedToast }, async () => {
      await requestGateway('memory.delete', { memory_id: memoryId, system_preset_id: PROFILE_PRESET_ID })

      if (!mountedRef.current) {
        return
      }

      setDrafts(previous => ({ ...previous, [key]: '' }))
      setRows(previous => previous.filter(row => row.id !== memoryId))
      onCount(previous => (previous === null ? previous : Math.max(0, previous - 1)))
      await load(true)
    })
  }

  const extraRows = rows.filter(row => !PROFILE_FIELDS.some(field => field.context === row.context))

  return (
    <section className="mb-6">
      <p className={cn(SECTION_TITLE, 'mb-2')}>{p.title}</p>
      <p className={cn(HINT_TEXT, 'mb-3')}>{p.hint}</p>
      {hint && <p className="mb-2 text-xs text-amber-300/90">{hint}</p>}

      {loading ? (
        <p className="text-xs text-muted">{t.loading}</p>
      ) : (
        <div className="space-y-2.5">
          {PROFILE_FIELDS.map(field => {
            const row = rowOf(field)
            const draft = drafts[field.key] ?? row?.content ?? ''

            return (
              <ProfileEntryEditor
                busy={!!busyKeys[field.key]}
                date={field.date}
                dirty={pendingValue(draft, row?.content) !== null}
                inputId={`profile-${field.key}`}
                key={field.key}
                label={`${p.fields[field.key]} · ${row ? p.set : p.unset}`}
                maxLength={MAX_USER_TEXT}
                multiline={field.multiline}
                onChange={value => setDrafts(previous => ({ ...previous, [field.key]: value }))}
                onDelete={row ? () => void remove(field.key, row.id) : undefined}
                onSave={() => void saveKnown(field)}
                persisted={!!row}
                value={draft}
              />
            )
          })}
          {extraRows.map(row => {
            const key = row.context ?? String(row.id)
            const draft = drafts[key] ?? row.content ?? ''

            return (
              <ProfileEntryEditor
                busy={!!busyKeys[key]}
                dirty={pendingValue(draft, row.content) !== null}
                inputId={`profile-memory-${row.id}`}
                key={row.id}
                label={row.context?.replace(USER_PROFILE_CONTEXT_PREFIX, '') || '—'}
                multiline
                onChange={value => setDrafts(previous => ({ ...previous, [key]: value }))}
                onDelete={() => void remove(key, row.id)}
                onSave={() => void saveExtra(row)}
                persisted
                value={draft}
              />
            )
          })}
        </div>
      )}
    </section>
  )
}

function ProfileEntryEditor({
  busy,
  date = false,
  dirty,
  inputId,
  label,
  maxLength,
  multiline = false,
  onChange,
  onDelete,
  onSave,
  persisted,
  value
}: ProfileEntryEditorProps): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.memory

  const inputProps = {
    className: cn(INPUT_CLASS, multiline && 'resize-none'),
    disabled: busy,
    id: inputId,
    maxLength,
    onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(event.target.value),
    value
  }

  return (
    <div className="liquid-glass-card rounded-2xl p-3.5">
      <label className={cn(HINT_TEXT, 'mb-2 block')} htmlFor={inputId}>
        {label}
      </label>
      {multiline ? (
        <textarea {...inputProps} rows={3} />
      ) : date ? (
        <DatePicker
          clearLabel={dict.common.clear}
          disabled={busy}
          id={inputId}
          onChange={onChange}
          placeholder={dict.common.pickDate}
          value={value}
        />
      ) : (
        <input {...inputProps} />
      )}
      <div className="mt-2 flex items-center gap-2">
        <button className={BTN_SUBTLE} disabled={busy || !dirty} onClick={onSave} type="button">
          {busy ? t.saving : persisted ? dict.common.save : t.profile.add}
        </button>
        {onDelete && (
          <button className={BTN_GHOST} disabled={busy} onClick={onDelete} type="button">
            {t.delete}
          </button>
        )}
        {persisted && !dirty && !busy && <span className={HINT_TEXT}>{t.saved}</span>}
      </div>
    </div>
  )
}
