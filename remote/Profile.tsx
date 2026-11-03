import type { ReactElement } from 'react'
import { useCallback, useEffect, useState } from 'react'

import type { PersonaEditableField, PersonaResponse } from '@protocol'

import type { ApiResponse } from '../shared/api'
import { isRecord } from '../shared/gateway'
import { uiCopy } from '../shared/ui-copy'

import type { RemoteApi } from './api'
import { ApiError } from './api'
import { saveWithReadback } from './save-result'
import { Media, Notice, useAlive, usePageScroll, usePageState, useTask, useVisiblePolling } from './ui'

const PERSONA_FIELDS: { key: PersonaEditableField; label: string; rows: number }[] = [
  { key: 'name', label: '名字', rows: 1 },
  { key: 'age', label: '年龄（可选）', rows: 1 },
  { key: 'occupation', label: '职业或身份（可选）', rows: 1 },
  { key: 'background', label: '背景经历（可选）', rows: 5 },
  { key: 'personality', label: '性格', rows: 3 },
  { key: 'relationship', label: '你们的关系', rows: 3 },
  { key: 'speaking_style', label: '说话方式', rows: 3 }
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
  const definition = persona ? definitionOf(persona) : {}
  const personaDirty = Boolean(draft && PERSONA_FIELDS.some(({ key }) => draft.values[key] !== (draft.base[key] ?? '')))
  const personaConflict = Boolean(
    draft && persona && PERSONA_FIELDS.some(({ key }) => draft.base[key] !== editableValue(definition, key))
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

  const beginPersonaEdit = () => {
    const base: PersonaDraft['base'] = {}
    const values: PersonaDraft['values'] = {}
    PERSONA_FIELDS.forEach(({ key }) => {
      base[key] = editableValue(definition, key)
      values[key] = base[key] ?? ''
    })
    setDraft({ base, values })
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
              definition_json: Object.fromEntries(PERSONA_FIELDS.map(({ key }) => [key, draft.values[key] ?? ''])),
              expected_fields: Object.fromEntries(PERSONA_FIELDS.map(({ key }) => [key, draft.base[key] ?? null]))
            }
          }),
        read: () => api.invoke('companion.get_persona'),
        matches: latest => {
          const latestDefinition = definitionOf(latest)

          return PERSONA_FIELDS.every(
            ({ key }) => (editableValue(latestDefinition, key) ?? '') === (draft.values[key] ?? '').trim()
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
        setSaved('基本资料已保存')
      }
    })

  const name = typeof definition.name === 'string' ? definition.name : '我的伙伴'

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
        <h2>{name}</h2>
        {persona?.current_mood ? <p>{persona.current_mood}</p> : null}
        <div className="tag-list">
          {persona?.personality_tags?.map(tag => (
            <span className="pill" key={tag}>
              {tag}
            </span>
          ))}
        </div>
      </section>
      <section className="card profile-editor">
        <div className="card-title">
          <h3>基本资料</h3>
          {!draft ? (
            <button disabled={!persona || busy} onClick={beginPersonaEdit} type="button">
              编辑资料
            </button>
          ) : null}
        </div>
        {draft ? (
          <>
            {personaDirty ? <p className="draft-label">未保存修改</p> : null}
            <div className="edit-fields">
              {PERSONA_FIELDS.map(({ key, label, rows }) => (
                <label className="field-label" key={key}>
                  {label}
                  <textarea
                    disabled={busy}
                    maxLength={key === 'background' ? 2000 : 500}
                    onChange={event =>
                      setDraft(previous =>
                        previous ? { ...previous, values: { ...previous.values, [key]: event.target.value } } : previous
                      )
                    }
                    rows={rows}
                    value={draft.values[key] ?? ''}
                  />
                </label>
              ))}
            </div>
            {personaConflict ? (
              <div className="creation-status">
                <p>资料已在其他设备修改。请核对最新内容后继续，当前草稿已保留。</p>
                <dl>
                  {PERSONA_FIELDS.map(({ key, label }) => (
                    <div key={key}>
                      <dt>{label}</dt>
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
                            base: Object.fromEntries(
                              PERSONA_FIELDS.map(({ key }) => [key, editableValue(definition, key)])
                            )
                          }
                        : previous
                    )
                  }
                  type="button"
                >
                  已核对，保留草稿继续编辑
                </button>
              </div>
            ) : null}
            <div className="button-row">
              <button
                className="primary"
                disabled={busy || !personaDirty || personaConflict}
                onClick={savePersona}
                type="button"
              >
                {busy ? uiCopy.saving : '保存修改'}
              </button>
              <button disabled={busy} onClick={() => setDraft(null)} type="button">
                取消
              </button>
            </div>
          </>
        ) : (
          <dl>
            {PERSONA_FIELDS.map(({ key, label }) => (
              <div key={key}>
                <dt>{label}</dt>
                <dd>{editableValue(definition, key) || '未填写'}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>
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
