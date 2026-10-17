import type { ReactElement } from 'react'
import { useCallback, useEffect, useState } from 'react'

import type {
  CharacterCard,
  CharacterFeatureKey,
  CharacterOverrides,
  PersonaEditableField,
  PersonaResponse
} from '@protocol'

import type { ApiResponse } from '../shared/api'

import type { RemoteApi } from './api'
import { ApiError, record } from './api'
import { Media, Notice, useAlive, usePageScroll, usePageState, useTask } from './ui'

const PERSONA_FIELDS: { key: PersonaEditableField; label: string; rows: number }[] = [
  { key: 'name', label: '名字', rows: 1 },
  { key: 'personality', label: '性格', rows: 3 },
  { key: 'relationship', label: '你们的关系', rows: 3 },
  { key: 'speaking_style', label: '说话方式', rows: 3 }
]

const FEATURE_GROUPS: { title: string; fields: { key: CharacterFeatureKey; label: string }[] }[] = [
  {
    title: '头部特征',
    fields: [
      { key: 'head_shape', label: '头部与脸部轮廓' },
      { key: 'facial_features', label: '五官' },
      { key: 'facial_surface', label: '面部材质与颜色' },
      { key: 'head_identifiers', label: '固有辨识特征' }
    ]
  },
  {
    title: '身体特征',
    fields: [
      { key: 'body_shape', label: '体型' },
      { key: 'proportions', label: '身体比例' },
      { key: 'limbs_and_appendages', label: '肢体与附属结构' },
      { key: 'body_surface', label: '身体材质与标记' }
    ]
  }
]

interface PersonaDraft {
  base: Partial<Record<PersonaEditableField, string | null>>
  values: Partial<Record<PersonaEditableField, string>>
}

interface CardDraft {
  avatarId: number
  revision: number
  changes: CharacterOverrides
}

type Avatar = ApiResponse<'companion.get_avatar'>

function definitionOf(persona: PersonaResponse): Record<string, unknown> {
  const value: unknown = JSON.parse(persona.definition_json)

  return record(value) ? value : {}
}

function editableValue(definition: Record<string, unknown>, key: PersonaEditableField): string | null {
  return typeof definition[key] === 'string' ? definition[key] : null
}

export function Profile({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [persona, setPersona] = useState<PersonaResponse | null>(null)
  const [avatar, setAvatar] = useState<Avatar | null>(null)
  const [card, setCard] = useState<CharacterCard | null>(null)
  const [draft, setDraft] = usePageState<PersonaDraft | null>('profile.draft', null)
  const [cardDraft, setCardDraft] = usePageState<CardDraft | null>('profile.cardDraft', null)
  const [saved, setSaved] = useState('')
  const definition = persona ? definitionOf(persona) : {}
  const personaDirty = Boolean(draft && PERSONA_FIELDS.some(({ key }) => draft.values[key] !== (draft.base[key] ?? '')))
  const personaConflict = Boolean(
    draft && persona && PERSONA_FIELDS.some(({ key }) => draft.base[key] !== editableValue(definition, key))
  )
  const cardDirty = Boolean(cardDraft && Object.keys(cardDraft.changes).length)
  const cardConflict = Boolean(
    cardDraft && persona && (!card || card.avatar_id !== cardDraft.avatarId || card.revision !== cardDraft.revision)
  )
  const analyzing = card?.status === 'pending' || card?.status === 'running'

  const load = useCallback(async () => {
    const [nextPersona, nextAvatar, nextCard] = await Promise.all([
      api.invoke('companion.get_persona'),
      api.invoke('companion.get_avatar').catch(failure => {
        if (failure instanceof ApiError && failure.status === 404) {
          return null
        }
        throw failure
      }),
      api.invoke('companion.get_character_card_route')
    ])

    if (alive()) {
      setPersona(nextPersona)
      setAvatar(nextAvatar)
      setCard(nextCard)
    }
  }, [alive, api])

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])

  useEffect(() => {
    const onFocus = () => {
      if (document.visibilityState === 'visible') {
        void runLatest(load)
      }
    }
    window.addEventListener('focus', onFocus)
    const timer = analyzing
      ? setInterval(() => {
          if (document.visibilityState === 'visible') {
            void runLatest(async () => {
              const next = await api.invoke('companion.get_character_card_route')
              if (alive()) {
                setCard(next)
              }
            })
          }
        }, 5000)
      : undefined

    return () => {
      window.removeEventListener('focus', onFocus)
      clearInterval(timer)
    }
  }, [alive, analyzing, api, load, runLatest])
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
      let updated: PersonaResponse
      try {
        updated = await api.invoke('companion.put_persona', {
          body: {
            definition_json: JSON.stringify(draft.values),
            expected_fields: Object.fromEntries(PERSONA_FIELDS.map(({ key }) => [key, draft.base[key] ?? null]))
          }
        })
      } catch (failure) {
        if (!alive()) {
          throw failure
        }
        let latest: PersonaResponse | null = null
        try {
          latest = await api.invoke('companion.get_persona')
        } catch {
          // 核对失败时保留编辑起点与原错误。
        }
        if (alive() && latest) {
          setPersona(latest)
        }
        const latestDefinition = latest ? definitionOf(latest) : {}
        if (
          !latest ||
          !PERSONA_FIELDS.every(
            ({ key }) => (editableValue(latestDefinition, key) ?? '') === (draft.values[key] ?? '').trim()
          )
        ) {
          throw failure
        }
        updated = latest
      }
      if (alive()) {
        setPersona(updated)
        setDraft(null)
        setSaved('基本资料已保存')
      }
    })

  const saveCard = () =>
    run(async () => {
      if (!cardDraft) {
        return
      }
      let updated: CharacterCard
      try {
        updated = await api.invoke('companion.patch_character_card_route', {
          body: {
            expected_avatar_id: cardDraft.avatarId,
            expected_revision: cardDraft.revision,
            changes: cardDraft.changes
          }
        })
      } catch (failure) {
        if (!alive()) {
          throw failure
        }
        let latest: CharacterCard | null | undefined
        try {
          latest = await api.invoke('companion.get_character_card_route')
        } catch {
          // 核对失败时保留草稿和修订号。
        }
        if (alive() && latest !== undefined) {
          setCard(latest)
        }
        if (
          !latest ||
          latest.avatar_id !== cardDraft.avatarId ||
          !FEATURE_GROUPS.every(group =>
            group.fields.every(({ key }) => {
              if (!Object.hasOwn(cardDraft.changes, key)) {
                return true
              }
              const value = cardDraft.changes[key]

              return value === null ? latest.overrides[key] == null : latest.overrides[key] === value?.trim()
            })
          )
        ) {
          throw failure
        }
        updated = latest
      }
      if (alive()) {
        setCard(updated)
        setCardDraft(null)
        setSaved('角色卡已保存')
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
                    maxLength={500}
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
                {busy ? '保存中…' : '保存修改'}
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
      <section className="card character-card profile-editor">
        <div className="card-title">
          <h3>角色卡</h3>
          {!cardDraft && card && card.revision > 0 ? (
            <button
              disabled={busy}
              onClick={() => {
                setCardDraft({ avatarId: card.avatar_id, revision: card.revision, changes: {} })
                setSaved('')
              }}
              type="button"
            >
              编辑角色卡
            </button>
          ) : null}
        </div>
        <p className="muted">根据已确认的形象整理，描述伙伴固定的外形特征。</p>
        {avatar && card?.avatar_id === avatar.id ? (
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
        {cardDraft && cardDirty ? <p className="draft-label">未保存修改</p> : null}
        {cardDraft ? <p className="muted">留空会清除这项描述；选择“恢复自动值”会重新采用形象分析结果。</p> : null}
        {card && card.revision > 0 ? (
          FEATURE_GROUPS.map(group => (
            <div className="feature-group" key={group.title}>
              <h3>{group.title}</h3>
              {cardDraft ? (
                <div className="edit-fields">
                  {group.fields.map(({ key, label }) => (
                    <div key={key}>
                      <div className="field-toolbar">
                        <label htmlFor={`character-${key}`}>{label}</label>
                        <button
                          disabled={busy}
                          onClick={() =>
                            setCardDraft(previous =>
                              previous ? { ...previous, changes: { ...previous.changes, [key]: null } } : previous
                            )
                          }
                          type="button"
                        >
                          恢复自动值
                        </button>
                      </div>
                      <textarea
                        disabled={busy}
                        id={`character-${key}`}
                        maxLength={1000}
                        onChange={event =>
                          setCardDraft(previous =>
                            previous
                              ? { ...previous, changes: { ...previous.changes, [key]: event.target.value } }
                              : previous
                          )
                        }
                        rows={3}
                        value={
                          Object.hasOwn(cardDraft.changes, key)
                            ? (cardDraft.changes[key] ?? card.automatic[key])
                            : card.features[key]
                        }
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <dl>
                  {group.fields.map(({ key, label }) => (
                    <div key={key}>
                      <dt>{label}</dt>
                      <dd>
                        {card.features[key] || '尚未整理'}
                        {card.overrides[key] != null ? <span className="pill">已编辑</span> : null}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          ))
        ) : !analyzing ? (
          <p className="muted">确认形象后，角色卡会显示在这里。</p>
        ) : null}
        {analyzing ? (
          <p aria-live="polite" className="creation-status">
            正在整理角色卡，可以先去其他页面。
          </p>
        ) : null}
        {card?.status === 'failed' ? <Notice error={card.error ?? '角色卡整理失败，请重新整理。'} /> : null}
        {cardConflict ? (
          <div className="creation-status">
            <p>角色卡已更新，草稿仍保留。请核对下面的最新特征后继续。</p>
            {card ? (
              <dl>
                {FEATURE_GROUPS.flatMap(group => group.fields).map(({ key, label }) => (
                  <div key={key}>
                    <dt>{label}</dt>
                    <dd>{card.features[key] || '尚未整理'}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
            {card && cardDraft?.avatarId === card.avatar_id ? (
              <button
                disabled={busy}
                onClick={() =>
                  setCardDraft(previous => (previous ? { ...previous, revision: card.revision } : previous))
                }
                type="button"
              >
                已核对，保留草稿继续编辑
              </button>
            ) : (
              <p>伙伴形象已更换，请取消草稿后重新编辑。</p>
            )}
          </div>
        ) : null}
        <div className="button-row">
          {cardDraft ? (
            <>
              <button
                className="primary"
                disabled={busy || !cardDirty || cardConflict}
                onClick={saveCard}
                type="button"
              >
                {busy ? '保存中…' : '保存修改'}
              </button>
              <button disabled={busy} onClick={() => setCardDraft(null)} type="button">
                取消
              </button>
            </>
          ) : card ? (
            <button
              disabled={busy || analyzing}
              onClick={() =>
                run(async () => {
                  const updated = await api.invoke('companion.extract_character_card_route', {
                    body: { expected_avatar_id: card.avatar_id, expected_revision: card.revision }
                  })
                  if (alive()) {
                    setCard(updated)
                  }
                })
              }
              type="button"
            >
              {card.status === 'failed' ? '重试整理角色卡' : '重新整理角色卡'}
            </button>
          ) : null}
        </div>
      </section>
    </>
  )
}
