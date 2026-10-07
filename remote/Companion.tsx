import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { RemoteApi, RemoteGateway } from './api'
import { record } from './api'
import { Assets } from './Assets'
import type { Diary, Memory, SystemPresetSummary } from './types'
import { Empty, Media, Notice, PageHeader, useAlive, useTask } from './ui'

type Section = 'profile' | 'diary' | 'memory' | 'scene' | 'outfit'

export function Companion({
  api,
  gateway,
  connected,
  refresh
}: {
  api: RemoteApi
  gateway: RemoteGateway
  connected: boolean
  refresh: number
}): ReactElement {
  const [section, setSection] = useState<Section>('profile')

  return (
    <div className="page">
      <PageHeader subtitle="关于伙伴，也关于一起积累的生活" title="伙伴" />
      <div aria-label="伙伴功能" className="section-tabs">
        {(
          [
            { id: 'profile', label: '资料' },
            { id: 'diary', label: '日记' },
            { id: 'memory', label: '记忆' },
            { id: 'scene', label: '场景' },
            { id: 'outfit', label: '衣柜' }
          ] as const
        ).map(item => (
          <button
            aria-pressed={section === item.id}
            className={section === item.id ? 'selected' : ''}
            key={item.id}
            onClick={() => setSection(item.id)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>
      {section === 'profile' ? (
        <Profile api={api} refresh={refresh} />
      ) : section === 'diary' ? (
        <DiaryView api={api} refresh={refresh} />
      ) : section === 'memory' ? (
        <MemoryView connected={connected} gateway={gateway} refresh={refresh} />
      ) : (
        <Assets api={api} key={section} kind={section} refresh={refresh} />
      )}
    </div>
  )
}

function Profile({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const alive = useAlive()
  const { runLatest, error } = useTask()
  const [avatar, setAvatar] = useState('')
  const [definition, setDefinition] = useState<Record<string, unknown>>({})
  const [tags, setTags] = useState<string[]>([])
  const [mood, setMood] = useState<string | null>(null)

  useEffect(() => {
    void runLatest(async () => {
      const [persona, image] = await Promise.all([
        api.request<{
          definition_json: string
          personality_tags: string[]
          current_mood: string | null
        }>('/api/companion/persona'),
        api.request<{ asset_url: string }>('/api/companion/avatar')
      ])

      if (!alive()) {
        return
      }

      const value: unknown = JSON.parse(persona.definition_json)
      setDefinition(record(value) ? value : {})
      setAvatar(image.asset_url)
      setTags(persona.personality_tags)
      setMood(persona.current_mood)
    })
  }, [api, refresh, alive, runLatest])

  const name =
    typeof definition.name === 'string'
      ? definition.name
      : typeof definition.persona_name === 'string'
        ? definition.persona_name
        : '我的伙伴'

  return (
    <>
      <section className="profile-card companion-profile">
        {avatar ? <Media type="image" url={avatar} /> : <div className="avatar">✦</div>}
        <h2>{name}</h2>
        {mood ? <p>{mood}</p> : null}
        <div className="tag-list">
          {tags.map(tag => (
            <span className="pill" key={tag}>
              {tag}
            </span>
          ))}
        </div>
      </section>
      {error ? <Notice>{error}</Notice> : null}
      <section className="card">
        <h3>关于伙伴</h3>
        <dl>
          {[
            { key: 'gender', label: '性别' },
            { key: 'age', label: '年龄' },
            { key: 'personality', label: '性格' },
            { key: 'background', label: '背景' },
            { key: 'description', label: '介绍' }
          ].map(field =>
            typeof definition[field.key] === 'string' ? (
              <div key={field.key}>
                <dt>{field.label}</dt>
                <dd>{String(definition[field.key])}</dd>
              </div>
            ) : null
          )}
        </dl>
        <p className="muted">聊天、动态与日记，会继续积累你们共同的生活。</p>
      </section>
    </>
  )
}

function DiaryView({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const alive = useAlive()
  const { runLatest, error } = useTask()
  const [entries, setEntries] = useState<Diary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const request = useRef(0)

  const load = useCallback(async () => {
    const revision = ++request.current
    const query = new URLSearchParams({ limit: '100' })

    if (from) {
      query.set('from', from)
    }

    if (to) {
      query.set('to', to)
    }

    const result = await api.request<{
      entries: Diary[]
      unread_diary_ids: string[]
    }>(`/api/companion/diary?${query}`)

    if (alive() && revision === request.current) {
      setEntries(result.entries)

      if (result.unread_diary_ids.length && document.visibilityState === 'visible') {
        await api.request('/api/companion/diary/read', 'POST', {
          diary_ids: result.unread_diary_ids
        })
      }
    }
  }, [api, alive, from, to])

  useEffect(() => {
    void runLatest(load)
  }, [refresh, load, runLatest])
  const entry = entries.find(value => value.id === selected)

  return (
    <>
      <div className="date-filter">
        <label>
          从
          <input onChange={event => setFrom(event.target.value)} type="date" value={from} />
        </label>
        <label>
          至
          <input onChange={event => setTo(event.target.value)} type="date" value={to} />
        </label>
      </div>
      {error ? <Notice>{error}</Notice> : null}
      {entry ? (
        <article className="diary-entry">
          <button className="subtle" onClick={() => setSelected(null)} type="button">
            ‹ 返回日记
          </button>
          <p className="eyebrow">{entry.entry_date}</p>
          <h2>{entry.title}</h2>
          {entry.mood ? <span className="pill">{entry.mood}</span> : null}
          <div className="diary-body">{entry.body}</div>
        </article>
      ) : entries.length ? (
        <div className="diary-list">
          {entries.map(value => (
            <button className="diary-preview" key={value.id} onClick={() => setSelected(value.id)} type="button">
              <time>{value.entry_date}</time>
              <h3>{value.title}</h3>
              <p>{value.body}</p>
              {value.mood ? <span className="pill">{value.mood}</span> : null}
            </button>
          ))}
        </div>
      ) : (
        <Empty title="日记还没写到这里">伙伴的第一人称日记会保存在这里。</Empty>
      )}
      {entries.length === 100 ? (
        <button
          className="load-more"
          onClick={() => {
            const earliest = entries.at(-1)?.entry_date

            if (earliest) {
              const date = new Date(`${earliest}T12:00:00`)
              date.setDate(date.getDate() - 1)
              setTo(
                `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
              )
              setSelected(null)
            }
          }}
          type="button"
        >
          查看更早的日记
        </button>
      ) : null}
    </>
  )
}

function MemoryView({
  gateway,
  connected,
  refresh
}: {
  gateway: RemoteGateway
  connected: boolean
  refresh: number
}): ReactElement {
  const { runLatest, error } = useTask()
  const alive = useAlive()
  const [presets, setPresets] = useState<SystemPresetSummary[]>([])
  const [preset, setPreset] = useState('companion')
  const [status, setStatus] = useState('active')
  const [kind, setKind] = useState('recall')

  useEffect(() => {
    if (connected) {
      void runLatest(async () => {
        const result = await gateway.request<{
          presets: SystemPresetSummary[]
        }>('system.list_presets')

        if (alive()) {
          setPresets(result.presets)
        }
      })
    }
  }, [gateway, connected, runLatest, alive])

  return (
    <>
      <div className="form-grid">
        <label>
          记忆作用域
          <select onChange={event => setPreset(event.target.value)} value={preset}>
            {presets.map(value => (
              <option key={value.id} value={value.id}>
                {value.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          类型
          <select onChange={event => setKind(event.target.value)} value={kind}>
            <option value="recall">共同记忆</option>
            <option value="user_profile">我的资料</option>
          </select>
        </label>
        <label>
          状态
          <select onChange={event => setStatus(event.target.value)} value={status}>
            <option value="active">有效</option>
            <option value="candidate">待确认</option>
            <option value="invalidated">已失效</option>
            <option value="expired">已过期</option>
          </select>
        </label>
      </div>
      {error ? <Notice>{error}</Notice> : null}
      <MemoryList
        connected={connected}
        gateway={gateway}
        key={`${preset}:${status}:${kind}`}
        kind={kind}
        preset={preset}
        refresh={refresh}
        status={status}
      />
    </>
  )
}

function MemoryList({
  gateway,
  connected,
  preset,
  refresh,
  status,
  kind
}: {
  gateway: RemoteGateway
  connected: boolean
  preset: string
  refresh: number
  status: string
  kind: string
}): ReactElement {
  const { run, runLatest, busy, error } = useTask()
  const alive = useAlive()
  const [rows, setRows] = useState<Memory[]>([])
  const [search, setSearch] = useState('')

  const load = useCallback(async () => {
    const result = await gateway.request<{ memories: Memory[] }>('memory.list', {
      system_preset_id: preset,
      status,
      kind
    })

    if (alive()) {
      setRows(result.memories)
    }
  }, [gateway, preset, status, kind, alive])

  useEffect(() => {
    if (connected) {
      void runLatest(load)
    }
  }, [connected, load, refresh, runLatest])

  return (
    <>
      <input
        aria-label="搜索记忆"
        className="search-input"
        onChange={event => setSearch(event.target.value)}
        placeholder="搜索这个作用域中的记忆"
        type="search"
        value={search}
      />
      {error ? <Notice>{error}</Notice> : null}
      {rows
        .filter(row => `${row.content ?? ''}${row.context ?? ''}`.includes(search))
        .map(row => (
          <MemoryEditor
            gateway={gateway}
            key={row.id}
            onDelete={() => setRows(previous => previous.filter(value => value.id !== row.id))}
            onSave={updated => setRows(previous => previous.map(value => (value.id === updated.id ? updated : value)))}
            preset={preset}
            row={row}
          />
        ))}
      {rows.length === 0 ? <Empty title="这里还没有记忆">聊天中学到的内容会按预设保存在这里。</Empty> : null}
      <button className="load-more" disabled={!connected || busy} onClick={() => run(load)} type="button">
        刷新记忆
      </button>
    </>
  )
}

function MemoryEditor({
  gateway,
  row,
  preset,
  onSave,
  onDelete
}: {
  gateway: RemoteGateway
  row: Memory
  preset: string
  onSave: (row: Memory) => void
  onDelete: () => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [draft, setDraft] = useState({
    content: row.content ?? '',
    baseContent: row.content ?? '',
    version: row.content_version
  })

  useEffect(() => {
    if (row.content_version !== draft.version && draft.content === draft.baseContent) {
      setDraft({ content: row.content ?? '', baseContent: row.content ?? '', version: row.content_version })
    }
  }, [row, draft])

  return (
    <article className="card memory-card">
      <p className="eyebrow">{row.context?.replace(/^(recall|user_profile):/, '') || '记忆'}</p>
      <textarea
        aria-label="记忆内容"
        onChange={event => setDraft(previous => ({ ...previous, content: event.target.value }))}
        rows={4}
        value={draft.content}
      />
      {error ? (
        <Notice>
          {error}
          <span>草稿已保留。若内容已在其他设备修改，请刷新后核对。</span>
        </Notice>
      ) : null}
      <div className="button-row">
        <button
          disabled={busy || !draft.content.trim() || draft.content === row.content}
          onClick={() =>
            run(async () => {
              const updated = await gateway.request<Memory>('memory.update', {
                memory_id: row.id,
                system_preset_id: preset,
                expected_version: draft.version,
                content: draft.content
              })

              if (alive()) {
                onSave(updated)
                setDraft({
                  content: updated.content ?? '',
                  baseContent: updated.content ?? '',
                  version: updated.content_version
                })
              }
            })
          }
          type="button"
        >
          保存
        </button>
        <button
          className="danger"
          disabled={busy}
          onClick={() =>
            run(async () => {
              await gateway.request('memory.delete', {
                memory_id: row.id,
                system_preset_id: preset
              })

              if (alive()) {
                onDelete()
              }
            })
          }
          type="button"
        >
          遗忘
        </button>
      </div>
    </article>
  )
}
