import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { MAX_USER_TEXT } from '@/modules/character'
import { useGatewayRequest } from '@/shared'
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
  deleteLabel: string
  dirty: boolean
  inputId: string
  label: string
  maxLength?: number
  multiline?: boolean
  onChange: (value: string) => void
  onDelete?: () => void
  onSave: () => void
  persisted: boolean
  saveLabel: string
  savedLabel: string
  value: string
}

const PROFILE_PRESET_ID = 'companion'

// 标签与后端资料槽位一一对应；未知标签保留为可编辑的自定义条目。
const PROFILE_FIELDS: readonly ProfileField[] = [
  { context: 'user_profile:preferred_name', key: 'user_call_name' },
  { context: 'user_profile:gender', key: 'user_gender' },
  { context: 'user_profile:birthday', key: 'user_birthday', date: true },
  { context: 'user_profile:hobbies', key: 'user_hobbies', multiline: true },
  { context: 'user_profile:freeform', key: 'user_freeform', multiline: true }
]

export function UserProfileSection({
  onCount
}: {
  onCount: React.Dispatch<React.SetStateAction<number | null>>
}): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.memory
  const p = t.profile
  const { requestGateway } = useGatewayRequest()

  const [rowsByContext, setRowsByContext] = useState<Record<string, ProfileMemoryRow>>({})
  const [extraRows, setExtraRows] = useState<ProfileMemoryRow[]>([])
  const [loading, setLoading] = useState(true)
  const [hint, setHint] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busyKeys, setBusyKeys] = useState<Record<string, boolean>>({})
  const loadIdRef = useRef(0)
  const mountedRef = useRef(true)

  const load = useCallback(async (): Promise<void> => {
    if (!mountedRef.current) {
      return
    }

    const id = ++loadIdRef.current
    setLoading(true)
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

      const known: Record<string, ProfileMemoryRow> = {}
      const extra: ProfileMemoryRow[] = []

      for (const row of res.memories) {
        if (PROFILE_FIELDS.some(field => field.context === row.context)) {
          known[row.context ?? ''] = row
        } else {
          extra.push(row)
        }
      }

      setRowsByContext(known)
      setExtraRows(extra)
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
  }, [onCount, requestGateway, t.loadFailedHint, t.loadFailedToast])

  useEffect(() => {
    mountedRef.current = true
    const requestRef = loadIdRef
    void load()

    return () => {
      mountedRef.current = false
      requestRef.current++
    }
  }, [load])

  const setBusy = (key: string, busy: boolean): void => {
    setBusyKeys(previous => {
      const next = { ...previous }

      if (busy) {
        next[key] = true
      } else {
        delete next[key]
      }

      return next
    })
  }

  const saveKnown = async (field: ProfileField): Promise<void> => {
    const row = rowsByContext[field.context]
    const value = (drafts[field.key] ?? row?.content ?? '').trim()

    if (!value || value === (row?.content ?? '').trim()) {
      return
    }

    setBusy(field.key, true)

    try {
      await requestGateway('onboarding.submit', { field: field.key, value })

      if (!mountedRef.current) {
        return
      }

      setDrafts(previous => ({ ...previous, [field.key]: value }))

      if (row) {
        setRowsByContext(previous => ({
          ...previous,
          [field.context]: { ...previous[field.context], content: value }
        }))
      } else {
        onCount(previous => (previous === null ? previous : previous + 1))
      }

      await load()
    } catch (err) {
      if (mountedRef.current) {
        setHint(t.saveFailedHint)
        notifyError(err, t.saveFailedToast)
      }
    } finally {
      if (mountedRef.current) {
        setBusy(field.key, false)
      }
    }
  }

  const saveExtra = async (row: ProfileMemoryRow): Promise<void> => {
    const key = row.context ?? String(row.id)
    const value = (drafts[key] ?? row.content ?? '').trim()

    if (!value || value === (row.content ?? '').trim()) {
      return
    }

    setBusy(key, true)

    try {
      const updated = await requestGateway<ProfileMemoryRow>('memory.update', {
        memory_id: row.id,
        content: value,
        system_preset_id: PROFILE_PRESET_ID
      })

      if (!mountedRef.current) {
        return
      }

      setDrafts(previous => ({ ...previous, [key]: updated.content ?? value }))
      setExtraRows(previous => previous.map(entry => (entry.id === row.id ? updated : entry)))
      await load()
    } catch (err) {
      if (mountedRef.current) {
        setHint(t.saveFailedHint)
        notifyError(err, t.saveFailedToast)
      }
    } finally {
      if (mountedRef.current) {
        setBusy(key, false)
      }
    }
  }

  const remove = async (key: string, memoryId: number): Promise<void> => {
    setBusy(key, true)

    try {
      await requestGateway('memory.delete', { memory_id: memoryId, system_preset_id: PROFILE_PRESET_ID })

      if (!mountedRef.current) {
        return
      }

      setDrafts(previous => ({ ...previous, [key]: '' }))
      setRowsByContext(previous =>
        Object.fromEntries(Object.entries(previous).filter(([, row]) => row.id !== memoryId))
      )
      setExtraRows(previous => previous.filter(row => row.id !== memoryId))
      onCount(previous => (previous === null ? previous : Math.max(0, previous - 1)))
      await load()
    } catch (err) {
      if (mountedRef.current) {
        setHint(t.deleteFailedHint)
        notifyError(err, t.deleteFailedToast)
      }
    } finally {
      if (mountedRef.current) {
        setBusy(key, false)
      }
    }
  }

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
            const row = rowsByContext[field.context]
            const draft = drafts[field.key] ?? row?.content ?? ''
            const dirty = draft.trim() !== '' && draft.trim() !== (row?.content ?? '').trim()
            const busy = !!busyKeys[field.key]

            return (
              <ProfileEntryEditor
                busy={busy}
                date={field.date}
                deleteLabel={t.delete}
                dirty={dirty}
                inputId={`profile-${field.key}`}
                key={field.key}
                label={`${p.fields[field.key]} · ${row ? p.set : p.unset}`}
                maxLength={MAX_USER_TEXT}
                multiline={field.multiline}
                onChange={value => setDrafts(previous => ({ ...previous, [field.key]: value }))}
                onDelete={row ? () => void remove(field.key, row.id) : undefined}
                onSave={() => void saveKnown(field)}
                persisted={!!row}
                savedLabel={t.saved}
                saveLabel={busy ? t.saving : row ? dict.common.save : p.add}
                value={draft}
              />
            )
          })}
          {extraRows.map(row => {
            const key = row.context ?? String(row.id)
            const draft = drafts[key] ?? row.content ?? ''
            const dirty = draft.trim() !== '' && draft.trim() !== (row.content ?? '').trim()
            const busy = !!busyKeys[key]

            return (
              <ProfileEntryEditor
                busy={busy}
                deleteLabel={t.delete}
                dirty={dirty}
                inputId={`profile-memory-${row.id}`}
                key={row.id}
                label={row.context ?? '—'}
                multiline
                onChange={value => setDrafts(previous => ({ ...previous, [key]: value }))}
                onDelete={() => void remove(key, row.id)}
                onSave={() => void saveExtra(row)}
                persisted
                savedLabel={t.saved}
                saveLabel={busy ? t.saving : dict.common.save}
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
  deleteLabel,
  dirty,
  inputId,
  label,
  maxLength,
  multiline = false,
  onChange,
  onDelete,
  onSave,
  persisted,
  saveLabel,
  savedLabel,
  value
}: ProfileEntryEditorProps): React.ReactElement {
  const dict = useStrings()

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
          {saveLabel}
        </button>
        {onDelete && (
          <button className={BTN_GHOST} disabled={busy} onClick={onDelete} type="button">
            {deleteLabel}
          </button>
        )}
        {persisted && !dirty && !busy && <span className={HINT_TEXT}>{savedLabel}</span>}
      </div>
    </div>
  )
}
