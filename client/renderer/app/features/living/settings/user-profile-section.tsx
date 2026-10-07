import { useStore } from '@nanostores/react'
import type React from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { MAX_USER_TEXT } from '@/modules/character'
import { requestGateway } from '@/shared'
import { useAsyncGuard } from '@/shared/hooks/use-async-guard'
import type { GatewayEvent } from '@/shared/lib/gateway-protocol'
import { isRecord } from '@/shared/lib/is-record'
import { cn } from '@/shared/lib/utils'
import { BTN_GHOST, BTN_SUBTLE, DatePicker, HINT_TEXT, INPUT_CLASS, SECTION_TITLE } from '@/shared/panel'
import { SpiritAgentGateway } from '@/shared/spiritagent'
import { $gateway } from '@/shared/store/gateway'
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
  content_version: number
  context: string | null
  content: string | null
}

export interface MemoryDraft {
  content: string
  baseContent: string
  baseVersion: number | null
}

export function editMemoryDraft(
  previous: MemoryDraft | undefined,
  content: string,
  row?: { content: string | null; content_version: number }
): MemoryDraft | undefined {
  const baseContent = previous?.baseContent ?? row?.content ?? ''

  return content === baseContent
    ? undefined
    : { content, baseContent, baseVersion: previous ? previous.baseVersion : (row?.content_version ?? null) }
}

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

    return gateway instanceof SpiritAgentGateway
      ? gateway.onEvent(onEvent)
      : window.spiritagent.onGatewayEvent(({ event }) => onEvent(event))
  }, [beginGuard, gateway, presetId, refresh])
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
  const [drafts, setDrafts] = useState<Record<string, MemoryDraft | undefined>>({})
  const [busyKeys, setBusyKeys] = useState<Record<string, boolean>>({})
  const loadIdRef = useRef(0)
  const beginGuard = useAsyncGuard()

  // background 静默刷新用于写后对账，不把表单切成 loading 占位。
  const load = useCallback(
    async (background = false): Promise<void> => {
      const isLive = beginGuard()
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

        if (!isLive() || loadIdRef.current !== id || res.system_preset_id !== PROFILE_PRESET_ID) {
          return
        }

        setRows(res.memories)
        onCount(res.counts.user_profile)
      } catch (err) {
        if (!isLive() || loadIdRef.current !== id) {
          return
        }

        setHint(t.loadFailedHint)
        notifyError(err, t.loadFailedToast)
      } finally {
        if (isLive() && loadIdRef.current === id) {
          setLoading(false)
        }
      }
    },
    [beginGuard, onCount, t.loadFailedHint, t.loadFailedToast]
  )

  useEffect(() => {
    void load()

    return () => {
      loadIdRef.current += 1
    }
  }, [load])

  const refresh = useCallback(() => load(true), [load])
  useMemoryChanged(PROFILE_PRESET_ID, refresh)

  const setBusy = (key: string, busy: boolean): void => {
    setBusyKeys(previous => ({ ...previous, [key]: busy }))
  }

  // 三个写操作共用的失败与收尾：卸载后不再回写，失败提示由调用方给出。
  const guarded = async (
    key: string,
    failure: { hint: string; toast: string },
    action: (isLive: () => boolean) => Promise<void>
  ): Promise<void> => {
    const isLive = beginGuard()
    setBusy(key, true)

    try {
      await action(isLive)
    } catch (err) {
      if (isLive()) {
        setHint(failure.hint)
        notifyError(err, failure.toast)
      }
    } finally {
      if (isLive()) {
        setBusy(key, false)
      }
    }
  }

  // 资料槽位的 context 在后端唯一（uq_memories_user_context），按 context 取首条即可。
  const rowOf = (field: ProfileField): ProfileMemoryRow | undefined => rows.find(row => row.context === field.context)

  const saveKnown = async (field: ProfileField): Promise<void> => {
    const row = rowOf(field)
    const draft = drafts[field.key]
    const value = pendingValue(draft?.content ?? row?.content ?? '', draft?.baseContent ?? row?.content)

    if (!value) {
      return
    }

    await guarded(field.key, { hint: t.saveFailedHint, toast: t.saveFailedToast }, async isLive => {
      await requestGateway('onboarding.submit', {
        field: field.key,
        value,
        expected_version: draft?.baseVersion ?? 0
      })

      if (!isLive()) {
        return
      }

      setDrafts(previous => ({ ...previous, [field.key]: undefined }))

      if (row) {
        setRows(previous =>
          previous.map(entry => (entry.context === field.context ? { ...entry, content: value } : entry))
        )
      }

      await load(true)
    })
  }

  const saveExtra = async (row: ProfileMemoryRow): Promise<void> => {
    const key = row.context ?? String(row.id)
    const draft = drafts[key]
    const value = pendingValue(draft?.content ?? row.content ?? '', draft?.baseContent ?? row.content)

    if (!value || draft?.baseVersion === undefined || draft.baseVersion === null) {
      return
    }

    await guarded(key, { hint: t.saveFailedHint, toast: t.saveFailedToast }, async isLive => {
      const updated = await requestGateway<ProfileMemoryRow>('memory.update', {
        memory_id: row.id,
        content: value,
        expected_version: draft.baseVersion,
        system_preset_id: PROFILE_PRESET_ID
      })

      if (!isLive()) {
        return
      }

      setDrafts(previous => ({ ...previous, [key]: undefined }))
      setRows(previous => previous.map(entry => (entry.id === row.id ? updated : entry)))
    })
  }

  const remove = async (key: string, memoryId: number): Promise<void> => {
    await guarded(key, { hint: t.deleteFailedHint, toast: t.deleteFailedToast }, async isLive => {
      await requestGateway('memory.delete', { memory_id: memoryId, system_preset_id: PROFILE_PRESET_ID })

      if (!isLive()) {
        return
      }

      setDrafts(previous => ({ ...previous, [key]: undefined }))
      setRows(previous => previous.filter(row => row.id !== memoryId))
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
            const edit = drafts[field.key]
            const draft = edit?.content ?? row?.content ?? ''

            return (
              <ProfileEntryEditor
                busy={!!busyKeys[field.key]}
                date={field.date}
                dirty={pendingValue(draft, edit?.baseContent ?? row?.content) !== null}
                inputId={`profile-${field.key}`}
                key={field.key}
                label={`${p.fields[field.key]} · ${row ? p.set : p.unset}`}
                maxLength={MAX_USER_TEXT}
                multiline={field.multiline}
                onChange={value =>
                  setDrafts(previous => ({
                    ...previous,
                    [field.key]: editMemoryDraft(previous[field.key], value, row)
                  }))
                }
                onDelete={row ? () => void remove(field.key, row.id) : undefined}
                onSave={() => void saveKnown(field)}
                persisted={!!row}
                value={draft}
              />
            )
          })}
          {extraRows.map(row => {
            const key = row.context ?? String(row.id)
            const edit = drafts[key]
            const draft = edit?.content ?? row.content ?? ''

            return (
              <ProfileEntryEditor
                busy={!!busyKeys[key]}
                dirty={pendingValue(draft, edit?.baseContent ?? row.content) !== null}
                inputId={`profile-memory-${row.id}`}
                key={row.id}
                label={row.context?.replace(USER_PROFILE_CONTEXT_PREFIX, '') || '—'}
                multiline
                onChange={value =>
                  setDrafts(previous => ({ ...previous, [key]: editMemoryDraft(previous[key], value, row) }))
                }
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
