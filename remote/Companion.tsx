import { IconChevronRight, IconHeart, IconPhoto, IconShirt, IconUser } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { RemoteApi, RemoteGateway } from './api'
import { record } from './api'
import { Assets } from './Assets'
import type { Diary, Memory, SystemPresetSummary } from './types'
import { Empty, Media, Notice, PageHeader, useAlive, usePageScroll, usePageState, useTask } from './ui'

type Section = 'home' | 'profile' | 'memory' | 'scene' | 'outfit'

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
  const [section, setSection] = usePageState<Section>('companion.section', 'home')
  const items = [
    { id: 'profile', title: '伙伴资料', description: '名字、性格和伙伴的故事', icon: IconUser },
    { id: 'outfit', title: '衣柜', description: '为伙伴设计一套新衣服', icon: IconShirt },
    { id: 'scene', title: '窗口背景', description: '描述一个喜欢的地方，让 AI 画出来', icon: IconPhoto },
    { id: 'memory', title: '共同记忆', description: '查看、修改或忘记聊过的事', icon: IconHeart }
  ] as const
  const selected = items.find(item => item.id === section)

  useEffect(() => {
    document.querySelector('.content')?.scrollTo({ top: 0 })
  }, [section])

  return (
    <div className="page">
      {section !== 'scene' && section !== 'outfit' ? (
        <>
          {selected ? (
            <button className="back-link" onClick={() => setSection('home')} type="button">
              ‹ 伙伴
            </button>
          ) : null}
          <PageHeader
            subtitle={selected?.description ?? '一起装扮，也一起积累生活'}
            title={selected?.title ?? '伙伴'}
          />
        </>
      ) : null}
      {section === 'home' ? (
        <>
          <Profile api={api} compact refresh={refresh} />
          <div aria-label="伙伴功能" className="companion-hub">
            {items.map(item => (
              <button className="hub-link" key={item.id} onClick={() => setSection(item.id)} type="button">
                <span className="hub-icon">
                  <item.icon aria-hidden size={24} stroke={1.6} />
                </span>
                <span>
                  <strong>{item.title}</strong>
                  <small>{item.description}</small>
                </span>
                <IconChevronRight aria-hidden size={18} />
              </button>
            ))}
          </div>
        </>
      ) : section === 'profile' ? (
        <Profile api={api} refresh={refresh} />
      ) : section === 'memory' ? (
        <MemoryView connected={connected} gateway={gateway} refresh={refresh} />
      ) : (
        <Assets api={api} key={section} kind={section} onBack={() => setSection('home')} refresh={refresh} />
      )}
    </div>
  )
}

function Profile({
  api,
  compact = false,
  refresh
}: {
  api: RemoteApi
  compact?: boolean
  refresh: number
}): ReactElement {
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
      <section className={`profile-card companion-profile ${compact ? 'compact' : ''}`}>
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
      {!compact ? (
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
      ) : null}
    </>
  )
}

export function DiaryView({
  api,
  onRead,
  refresh
}: {
  api: RemoteApi
  onRead: () => void
  refresh: number
}): ReactElement {
  const alive = useAlive()
  const { runLatest, error } = useTask()
  const [entries, setEntries] = useState<Diary[]>([])
  const [loaded, setLoaded] = useState(false)
  const [selected, setSelected] = usePageState<string | null>('diary.selected', null)
  const [from, setFrom] = usePageState('diary.from', '')
  const [to, setTo] = usePageState('diary.to', '')
  const onReadRef = useRef(onRead)
  onReadRef.current = onRead
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
      setLoaded(true)

      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
      if (
        alive() &&
        revision === request.current &&
        result.unread_diary_ids.length &&
        document.visibilityState === 'visible' &&
        document.hasFocus()
      ) {
        await api.request('/api/companion/diary/read', 'POST', {
          diary_ids: result.unread_diary_ids
        })
        if (alive() && revision === request.current) {
          onReadRef.current()
        }
      }
    }
  }, [api, alive, from, to])

  useEffect(() => {
    void runLatest(load)
  }, [refresh, load, runLatest])
  const entry = entries.find(value => value.id === selected)
  usePageScroll(`diary.${selected ?? 'list'}.${from}.${to}`, loaded)

  return (
    <>
      {!entry ? (
        <details className="filter-details">
          <summary>按日期查找</summary>
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
        </details>
      ) : null}
      {error ? <Notice>{error}</Notice> : null}
      {entry ? (
        <article className="diary-entry">
          <button className="subtle" onClick={() => setSelected(null)} type="button">
            ‹ 返回日记
          </button>
          <p className="eyebrow">{entry.entry_date}</p>
          <h2>{entry.title}</h2>
          <div className="diary-body">{entry.body}</div>
        </article>
      ) : entries.length ? (
        <div className="diary-list">
          {entries.map(value => (
            <button className="diary-preview" key={value.id} onClick={() => setSelected(value.id)} type="button">
              <time>{value.entry_date}</time>
              <h3>{value.title}</h3>
              <p>{value.body}</p>
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
  const [preset, setPreset] = usePageState('memory.preset', 'companion')
  const [status, setStatus] = usePageState('memory.status', 'active')
  const [kind, setKind] = usePageState('memory.kind', 'recall')

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
      <p className="muted">伙伴会记住聊天中重要的事。你可以随时修改，或让它忘记。</p>
      <div aria-label="记忆内容" className="section-tabs feed-tabs">
        {(
          [
            { id: 'recall', label: '共同记忆' },
            { id: 'user_profile', label: '我的资料' }
          ] as const
        ).map(item => (
          <button
            aria-pressed={kind === item.id}
            className={kind === item.id ? 'selected' : ''}
            key={item.id}
            onClick={() => setKind(item.id)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>
      <details className="filter-details">
        <summary>
          筛选记忆 · {presets.find(value => value.id === preset)?.name ?? '伙伴对话'}
          {status === 'active' ? '' : ' · 其他状态'}
        </summary>
        <div className="form-grid">
          <label>
            哪些对话的记忆
            <select onChange={event => setPreset(event.target.value)} value={preset}>
              {presets.map(value => (
                <option key={value.id} value={value.id}>
                  {value.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            状态
            <select onChange={event => setStatus(event.target.value)} value={status}>
              <option value="active">正在记住</option>
              <option value="candidate">待确认</option>
              <option value="invalidated">已不适用</option>
              <option value="expired">已过期</option>
            </select>
          </label>
        </div>
      </details>
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
        placeholder="搜索这些对话里记住的内容"
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
      {rows.length === 0 ? <Empty title="这里还没有记忆">聊过的重要事情会保存在所属对话类型中。</Empty> : null}
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
  const [draft, setDraft] = usePageState(`memory.${preset}.${row.id}`, {
    content: row.content ?? '',
    baseContent: row.content ?? '',
    version: row.content_version
  })
  const conflict = row.content_version !== draft.version

  useEffect(() => {
    if (row.content_version !== draft.version && draft.content === draft.baseContent) {
      setDraft({ content: row.content ?? '', baseContent: row.content ?? '', version: row.content_version })
    }
  }, [row, draft, setDraft])

  return (
    <article className="card memory-card">
      <p className="eyebrow">{row.context?.replace(/^(recall|user_profile):/, '') || '记忆'}</p>
      <textarea
        aria-label="记忆内容"
        disabled={busy}
        onChange={event => setDraft(previous => ({ ...previous, content: event.target.value }))}
        rows={4}
        value={draft.content}
      />
      {conflict ? (
        <div className="creation-status">
          <p>这条记忆已在其他设备修改。请先核对最新内容，再选择如何继续。</p>
          <p className="post-body">{row.content}</p>
          <div className="button-row">
            <button
              disabled={busy}
              onClick={() =>
                setDraft({
                  content: row.content ?? '',
                  baseContent: row.content ?? '',
                  version: row.content_version
                })
              }
              type="button"
            >
              改用最新内容
            </button>
            <button
              disabled={busy}
              onClick={() =>
                setDraft(previous => ({
                  ...previous,
                  baseContent: row.content ?? '',
                  version: row.content_version
                }))
              }
              type="button"
            >
              保留草稿，继续编辑
            </button>
          </div>
        </div>
      ) : null}
      {error ? (
        <Notice error={error}>
          <span>草稿已保留。若内容已在其他设备修改，请刷新后核对。</span>
        </Notice>
      ) : null}
      <div className="button-row">
        <button
          disabled={busy || conflict || !draft.content.trim() || draft.content === row.content}
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
              if (!window.confirm('让伙伴忘记这条内容？此操作无法撤销。')) {
                return
              }
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
