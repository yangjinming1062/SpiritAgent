import type { ReactElement } from 'react'
import { useCallback, useEffect, useState } from 'react'

import type { PersonaEditableField, PersonaResponse } from '@protocol'

import type { ApiResponse } from '../shared/api'
import { isRecord } from '../shared/is-record'
import { uiCopy } from '../shared/ui-copy'

import type { RemoteApi } from './api'
import { ApiError } from './api'
import { usePageScroll, usePageState } from './page-state'
import { saveWithReadback } from './save-result'
import { useAlive, useTask, useVisiblePolling } from './tasks'
import { Media, Notice } from './ui'

const FIELD_LABELS: Record<PersonaEditableField, string> = {
  name: '名字',
  age: '年龄（可选）',
  occupation: '职业或身份（可选）',
  background: '背景经历（可选）',
  personality: '性格',
  relationship: '你们的关系',
  speaking_style: '说话方式'
}

const FIELD_ROWS: Record<PersonaEditableField, number> = {
  name: 1,
  age: 1,
  occupation: 1,
  background: 5,
  personality: 3,
  relationship: 3,
  speaking_style: 3
}

const PERSONA_FIELDS: PersonaEditableField[] = [
  'name',
  'age',
  'occupation',
  'background',
  'personality',
  'relationship',
  'speaking_style'
]

type ProfileGroupId = 'background' | 'bond'

/** 与桌面一致的分组：名字在名片上改，分组卡只承载相处方式与背景。 */
const GROUPS: { fields: PersonaEditableField[]; id: ProfileGroupId; title: string }[] = [
  { id: 'bond', title: '相处方式', fields: ['relationship', 'personality', 'speaking_style'] },
  { id: 'background', title: '背景', fields: ['age', 'occupation', 'background'] }
]

interface PersonaDraft {
  base: Partial<Record<PersonaEditableField, string | null>>
  values: Partial<Record<PersonaEditableField, string>>
}

type Avatar = ApiResponse<'companion.get_avatar'>

function definitionOf(persona: PersonaResponse): Record<string, unknown> {
  const value = persona.definition_json

  return isRecord(value) ? value : {}
}

function editableValue(definition: Record<string, unknown>, key: PersonaEditableField): string | null {
  return typeof definition[key] === 'string' ? definition[key] : null
}

export function Profile({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [persona, setPersona] = useState<PersonaResponse | null>(null)
  const [avatar, setAvatar] = useState<Avatar | null>(null)
  const [draft, setDraft] = usePageState<PersonaDraft | null>('profile.draft', null)
  const [saved, setSaved] = useState('')
  const [editingName, setEditingName] = useState(false)
  const [openGroups, setOpenGroups] = useState<ProfileGroupId[]>([])
  const definition = persona ? definitionOf(persona) : {}
  const dirty = Boolean(draft && PERSONA_FIELDS.some(key => (draft.values[key] ?? '') !== (draft.base[key] ?? '')))
  const personaConflict = Boolean(
    draft && PERSONA_FIELDS.some(key => draft.base[key] !== editableValue(definition, key))
  )
  const load = useCallback(async () => {
    const [nextPersona, nextAvatar] = await Promise.all([
      api.invoke('companion.get_persona'),
      api.invoke('companion.get_avatar').catch(failure => {
        if (failure instanceof ApiError && failure.status === 404) {
          return null
        }
        throw failure
      })
    ])

    if (alive()) {
      setPersona(nextPersona)
      setAvatar(nextAvatar)
    }
  }, [alive, api])

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])

  useVisiblePolling(
    avatar?.preparation_status === 'queued' || avatar?.preparation_status === 'running',
    load,
    runLatest
  )
  usePageScroll('companion.profile', persona !== null)

  const startDraft = (): PersonaDraft => {
    const base: PersonaDraft['base'] = {}
    const values: PersonaDraft['values'] = {}

    PERSONA_FIELDS.forEach(key => {
      base[key] = editableValue(definition, key)
      values[key] = base[key] ?? ''
    })

    return { base, values }
  }

  const ensureDraft = (): void => {
    if (!draft) {
      setDraft(startDraft())
      setSaved('')
    }
  }

  const updateField = (key: PersonaEditableField, value: string): void => {
    setDraft(previous => {
      const next = previous ?? startDraft()

      return { ...next, values: { ...next.values, [key]: value } }
    })
    setSaved('')
  }

  const savePersona = () =>
    run(async () => {
      if (!draft) {
        return
      }
      const updated = await saveWithReadback({
        write: () =>
          api.invoke('companion.put_persona', {
            body: {
              definition_json: Object.fromEntries(PERSONA_FIELDS.map(key => [key, draft.values[key] ?? ''])),
              expected_fields: Object.fromEntries(PERSONA_FIELDS.map(key => [key, draft.base[key] ?? null]))
            }
          }),
        read: () => api.invoke('companion.get_persona'),
        matches: latest => {
          const latestDefinition = definitionOf(latest)

          return PERSONA_FIELDS.every(
            key => (editableValue(latestDefinition, key) ?? '') === (draft.values[key] ?? '').trim()
          )
        },
        isCurrent: alive,
        onUnconfirmed: latest => {
          if (latest) {
            setPersona(latest)
          }
        }
      })
      if (alive()) {
        setPersona(updated)
        setDraft(null)
        setOpenGroups([])
        setEditingName(false)
        setSaved('基本资料已保存')
      }
    })

  const currentName = (draft?.values.name ?? editableValue(definition, 'name') ?? '').trim() || '我的伙伴'
  const unfilled = (group: { fields: PersonaEditableField[] }): number =>
    group.fields.filter(key => !(draft?.values[key] ?? editableValue(definition, key) ?? '').trim()).length

  return (
    <>
      {error ? (
        <Notice error={error}>
          <p>未保存的修改已保留。</p>
          <button disabled={busy} onClick={() => run(load)} type="button">
            刷新资料
          </button>
        </Notice>
      ) : null}
      {saved ? (
        <p aria-live="polite" className="save-status">
          {saved}
        </p>
      ) : null}
      <section className="profile-card companion-profile">
        {avatar?.asset_url ? <Media type="image" url={avatar.asset_url} /> : <div className="avatar">✦</div>}
        <div className="profile-name-row">
          {editingName ? (
            <input
              aria-label="名字"
              autoFocus
              disabled={busy}
              maxLength={500}
              onChange={event => updateField('name', event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter' || event.key === 'Escape') {
                  event.preventDefault()
                  setEditingName(false)
                }
              }}
              value={draft?.values.name ?? ''}
            />
          ) : (
            <h2>{currentName}</h2>
          )}
          <button
            aria-expanded={editingName}
            className="link-button"
            disabled={busy || !persona}
            onClick={() => {
              ensureDraft()
              setEditingName(value => !value)
            }}
            type="button"
          >
            {editingName ? '完成' : '改名'}
          </button>
        </div>
        <div className="tag-list">
          {persona?.personality_tags?.map(tag => (
            <span className="pill" key={tag}>
              {tag}
            </span>
          ))}
        </div>
      </section>
      {GROUPS.map(group => {
        const open = openGroups.includes(group.id)
        const missing = unfilled(group)

        return (
          <section className="card profile-editor" key={group.id}>
            <div className="card-title">
              <h3>
                {group.title}
                {missing ? <span className="draft-label">{missing} 项未填</span> : null}
              </h3>
              <button
                aria-expanded={open}
                disabled={busy || !persona}
                onClick={() => {
                  ensureDraft()
                  setOpenGroups(current =>
                    current.includes(group.id) ? current.filter(id => id !== group.id) : [...current, group.id]
                  )
                }}
                type="button"
              >
                {open ? '收起' : '编辑'}
              </button>
            </div>
            {open ? (
              <div className="edit-fields">
                {group.fields.map(key => (
                  <label className="field-label" key={key}>
                    {FIELD_LABELS[key]}
                    <textarea
                      disabled={busy}
                      maxLength={key === 'background' ? 2000 : 500}
                      onChange={event => updateField(key, event.target.value)}
                      placeholder="未填写"
                      rows={FIELD_ROWS[key]}
                      value={draft?.values[key] ?? ''}
                    />
                  </label>
                ))}
              </div>
            ) : (
              <dl className="profile-read">
                {group.fields.map(key => {
                  const value = (draft?.values[key] ?? editableValue(definition, key) ?? '').trim()

                  return (
                    <div key={key}>
                      <dt>{FIELD_LABELS[key]}</dt>
                      <dd className={value ? '' : 'placeholder'}>{value || '未填写'}</dd>
                    </div>
                  )
                })}
              </dl>
            )}
          </section>
        )
      })}
      {personaConflict ? (
        <section className="card">
          <p>资料已在其他设备修改。请核对最新内容后继续，当前草稿已保留。</p>
          <dl>
            {PERSONA_FIELDS.map(key => (
              <div key={key}>
                <dt>{FIELD_LABELS[key]}</dt>
                <dd>{editableValue(definition, key) || '未填写'}</dd>
              </div>
            ))}
          </dl>
          <button
            disabled={busy}
            onClick={() =>
              setDraft(previous =>
                previous
                  ? {
                      ...previous,
                      base: Object.fromEntries(PERSONA_FIELDS.map(key => [key, editableValue(definition, key)]))
                    }
                  : previous
              )
            }
            type="button"
          >
            已核对，保留草稿继续编辑
          </button>
        </section>
      ) : null}
      {dirty || personaConflict ? (
        <div className="button-row save-row">
          <button className="primary" disabled={busy || !dirty || personaConflict} onClick={savePersona} type="button">
            {busy ? uiCopy.saving : '保存修改'}
          </button>
          <button
            disabled={busy}
            onClick={() => {
              setDraft(null)
              setOpenGroups([])
              setEditingName(false)
              setSaved('')
            }}
            type="button"
          >
            撤销
          </button>
        </div>
      ) : null}
      <section className="card profile-editor">
        <h3>确认的形象</h3>
        {avatar ? (
          <div className="character-images">
            {[
              { url: avatar.asset_url, label: '确认的头像' },
              { url: avatar.is_fullbody_confirmed ? avatar.seed_fullbody_url : '', label: '确认的全身形象' }
            ].map(({ url, label }) =>
              url ? (
                <figure key={label}>
                  <Media type="image" url={url} />
                  <figcaption>{label}</figcaption>
                </figure>
              ) : null
            )}
          </div>
        ) : null}
        {avatar?.preparation_status === 'queued' || avatar?.preparation_status === 'running' ? (
          <p role="status">正在准备透明形象和默认造型，可以继续其他操作。</p>
        ) : null}
        {avatar?.preparation_error ? <Notice error={avatar.preparation_error} /> : null}
        {avatar?.preparation_status && ['failed', 'paused', 'unknown'].includes(avatar.preparation_status) ? (
          <button
            disabled={busy}
            onClick={() =>
              run(async () => {
                const next = await api.invoke('companion.retry_appearance_preparation', {
                  path: { avatar_id: avatar.id },
                  body: { expected_revision: avatar.identity_revision }
                })
                if (alive()) {
                  setAvatar(next)
                }
              })
            }
            type="button"
          >
            重试素材准备
          </button>
        ) : null}
      </section>
    </>
  )
}
