import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { MemoryCounts, MemoryItem, SystemPresetSummary, UserProfileField } from '@protocol'

import type { ApiInput } from '../shared/api'

import type { RemoteApi } from './api'
import { Empty, focusAfterRender, Notice, useAlive, usePageScroll, usePageState, useTask } from './ui'

type Status = 'active' | 'candidate' | 'invalidated' | 'expired'
const STATUSES: { id: Status; label: string }[] = [
  { id: 'active', label: '正在使用' },
  { id: 'candidate', label: '待整理' },
  { id: 'invalidated', label: '已停用' },
  { id: 'expired', label: '已过期' }
]

const PROFILE_FIELDS: { key: UserProfileField; context: string; label: string; rows: number }[] = [
  { key: 'user_call_name', context: 'user_profile:preferred_name', label: '称呼', rows: 1 },
  { key: 'user_gender', context: 'user_profile:gender', label: '性别', rows: 1 },
  { key: 'user_birthday', context: 'user_profile:birthday', label: '生日', rows: 1 },
  { key: 'user_hobbies', context: 'user_profile:hobbies', label: '爱好', rows: 2 },
  { key: 'user_freeform', context: 'user_profile:freeform', label: '补充信息', rows: 3 }
]

function memoryTitle(row: MemoryItem): string {
  return row.context?.replace(/^(recall|user_profile):/, '').replaceAll('_', ' ') || '记忆'
}

function updatedLabel(value: string): string {
  const date = new Date(value)

  return Number.isFinite(date.getTime())
    ? date.toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })
    : value
}

async function* readActiveMemories(
  api: RemoteApi,
  query: ApiInput<'resource_catalog.get_memories'>['query']
): AsyncGenerator<MemoryItem[]> {
  let offset = 0
  while (true) {
    const result = await api.invoke('resource_catalog.get_memories', {
      query: { ...query, status: 'active', limit: 500, offset }
    })
    yield result.memories
    offset += result.memories.length
    if (!result.memories.length || offset >= result.total) {
      return
    }
  }
}

async function readProfile(api: RemoteApi): Promise<MemoryItem[]> {
  const memories: MemoryItem[] = []
  for await (const page of readActiveMemories(api, { system_preset_id: 'companion', kind: 'user_profile' })) {
    memories.push(...page)
  }

  return memories
}

async function findSavedMemory(
  api: RemoteApi,
  preset: string,
  row: MemoryItem,
  content: string
): Promise<MemoryItem | null> {
  for await (const page of readActiveMemories(api, {
    system_preset_id: preset,
    kind: row.context?.startsWith('user_profile:') ? 'user_profile' : 'recall',
    q: content.trim()
  })) {
    const saved = page.find(value => value.id === row.id)
    if (saved) {
      return saved
    }
  }

  return null
}

export function MemoryView({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const { runLatest, error } = useTask()
  const alive = useAlive()
  const [presets, setPresets] = useState<SystemPresetSummary[]>([])
  const [preset, setPreset] = usePageState('memory.preset', 'companion')
  const [kind, setKind] = usePageState('memory.kind', 'recall')
  const profile = preset === 'companion' && kind === 'user_profile'

  useEffect(() => {
    void runLatest(async () => {
      const result = await api.invoke('resource_catalog.get_presets')
      if (alive()) {
        setPresets(result.presets)
      }
    })
  }, [alive, api, runLatest])

  return (
    <>
      <label className="field-label">
        查看谁的记忆
        <select onChange={event => setPreset(event.target.value)} value={preset}>
          {!presets.some(value => value.id === preset) ? (
            <option value={preset}>{preset === 'companion' ? '伙伴对话' : preset}</option>
          ) : null}
          {presets.map(value => (
            <option key={value.id} value={value.id}>
              {value.name}
            </option>
          ))}
        </select>
      </label>
      <div aria-label="记忆内容" className="section-tabs memory-tabs">
        <button
          aria-pressed={!profile}
          className={!profile ? 'selected' : ''}
          onClick={() => setKind('recall')}
          type="button"
        >
          记住的事
        </button>
        {preset === 'companion' ? (
          <button
            aria-pressed={profile}
            className={profile ? 'selected' : ''}
            onClick={() => setKind('user_profile')}
            type="button"
          >
            我的资料
          </button>
        ) : null}
      </div>
      {error ? <Notice>{error}</Notice> : null}
      {profile ? (
        <UserProfile api={api} refresh={refresh} />
      ) : (
        <MemoryList api={api} key={preset} preset={preset} refresh={refresh} />
      )}
    </>
  )
}

function MemoryList({ api, preset, refresh }: { api: RemoteApi; preset: string; refresh: number }): ReactElement {
  const { run, runLatest, busy, error } = useTask()
  const alive = useAlive()
  const [rows, setRows] = useState<MemoryItem[]>([])
  const [counts, setCounts] = useState<MemoryCounts | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [total, setTotal] = useState(0)
  const [status, setStatus] = usePageState<Status>(`memory.${preset}.status`, 'active')
  const [search, setSearch] = usePageState(`memory.${preset}.search`, '')
  const [query, setQuery] = useState(search)
  const [offset, setOffset] = usePageState(`memory.${preset}.offset`, 0)
  const [selected, setSelected] = usePageState<number | null>(`memory.${preset}.selected`, null)
  const [showEditor, setShowEditor] = usePageState(`memory.${preset}.showEditor`, false)
  const request = useRef(0)
  const selectedRef = useRef(selected)
  selectedRef.current = selected

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search), 250)

    return () => clearTimeout(timer)
  }, [search])

  const load = useCallback(async () => {
    const revision = ++request.current
    const result = await api.invoke('resource_catalog.get_memories', {
      query: {
        system_preset_id: preset,
        status,
        kind: 'recall',
        q: query,
        limit: 20,
        offset
      }
    })
    if (alive() && revision === request.current) {
      setRows(result.memories)
      setTotal(result.total)
      setCounts(result.counts)
      setLoaded(true)
      if (offset > 0 && offset >= result.total) {
        setOffset(Math.max(0, Math.ceil(result.total / 20) - 1) * 20)
      }
      if (!result.memories.some(row => row.id === selectedRef.current)) {
        setSelected(result.memories[0]?.id ?? null)
      }
    }
  }, [alive, api, offset, preset, query, setOffset, setSelected, status])

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])
  usePageScroll(`memory.${preset}.${status}.${offset}.${query}.${showEditor ? selected : 'list'}`, loaded)
  const selectedRow = rows.find(row => row.id === selected)

  return (
    <>
      <p className="muted">查看伙伴记住的事情，修改后会重新生效。待整理内容由伙伴继续整理。</p>
      <input
        aria-label="搜索记忆"
        className="search-input"
        onChange={event => {
          request.current++
          setSearch(event.target.value)
          setOffset(0)
          setShowEditor(false)
        }}
        placeholder="搜索这个分类里的全部记忆"
        type="search"
        value={search}
      />
      <div aria-label="记忆状态" className="section-tabs memory-status-tabs">
        {STATUSES.map(item => (
          <button
            aria-pressed={status === item.id}
            className={status === item.id ? 'selected' : ''}
            key={item.id}
            onClick={() => {
              request.current++
              setStatus(item.id)
              setOffset(0)
              setShowEditor(false)
            }}
            type="button"
          >
            {item.label}
            {counts ? ` · ${counts[item.id]}` : ''}
          </button>
        ))}
      </div>
      {error ? (
        <Notice error={error}>
          <button disabled={busy} onClick={() => run(load)} type="button">
            重新加载记忆
          </button>
        </Notice>
      ) : null}
      <div className="memory-workspace">
        <div className={`memory-layout ${showEditor && selectedRow ? 'show-editor' : ''}`}>
          <div className="memory-list-pane">
            <div aria-label="记忆列表" className="memory-summary-list">
              {rows.map(row => (
                <button
                  aria-pressed={row.id === selected}
                  className="memory-summary"
                  data-memory-id={row.id}
                  key={row.id}
                  onClick={() => {
                    setSelected(row.id)
                    setShowEditor(true)
                  }}
                  type="button"
                >
                  <strong>{row.content}</strong>
                  <span>{memoryTitle(row)}</span>
                  <time dateTime={row.updated_at}>{updatedLabel(row.updated_at)}</time>
                </button>
              ))}
            </div>
            {!rows.length && loaded && !busy ? (
              <Empty title={query ? '没有找到相关记忆' : '这里还没有记忆'}>
                {query ? '试试其他关键词。' : '聊过的重要事情会保存在所属对话分类中。'}
              </Empty>
            ) : null}
            <div aria-label="记忆分页" className="memory-pagination">
              <button
                disabled={busy || offset === 0}
                onClick={() => {
                  setOffset(Math.max(0, offset - 20))
                  setShowEditor(false)
                }}
                type="button"
              >
                上一页
              </button>
              <span>
                {total ? `${Math.floor(offset / 20) + 1} / ${Math.ceil(total / 20)}` : '0'} · 共 {total} 条
              </span>
              <button
                disabled={busy || offset + 20 >= total}
                onClick={() => {
                  setOffset(offset + 20)
                  setShowEditor(false)
                }}
                type="button"
              >
                下一页
              </button>
            </div>
          </div>
          <div className="memory-editor-pane">
            {selectedRow ? (
              <>
                <button
                  className="back-link memory-return"
                  onClick={() => {
                    setShowEditor(false)
                    focusAfterRender(`[data-memory-id="${selectedRow.id}"]`)
                  }}
                  type="button"
                >
                  ← 返回
                </button>
                <MemoryEditor
                  api={api}
                  key={selectedRow.id}
                  onChanged={() => runLatest(load)}
                  preset={preset}
                  row={selectedRow}
                />
              </>
            ) : null}
          </div>
        </div>
      </div>
      <button className="load-more" disabled={busy} onClick={() => run(load)} type="button">
        刷新记忆
      </button>
    </>
  )
}

function MemoryEditor({
  api,
  row,
  preset,
  onChanged
}: {
  api: RemoteApi
  row: MemoryItem
  preset: string
  onChanged: () => Promise<void>
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [draft, setDraft] = usePageState(`memory.${preset}.${row.id}`, {
    content: row.content,
    baseContent: row.content,
    version: row.content_version
  })
  const [saved, setSaved] = useState(false)
  const dirty = draft.content !== draft.baseContent
  const conflict = row.content_version > draft.version
  const active = row.status === 'active' && (!row.expires_at || Date.parse(row.expires_at) > Date.now())

  useEffect(() => {
    if (row.content_version > draft.version && !dirty) {
      setDraft({ content: row.content, baseContent: row.content, version: row.content_version })
    }
  }, [dirty, draft.version, row, setDraft])

  return (
    <article className="card memory-card profile-editor">
      <p className="eyebrow">{memoryTitle(row)}</p>
      <p className="muted">更新于 {updatedLabel(row.updated_at)}</p>
      {dirty ? (
        <p className="draft-label">未保存修改</p>
      ) : saved ? (
        <p aria-live="polite" className="save-status">
          修改已保存
        </p>
      ) : null}
      <label className="field-label">
        记忆内容
        <textarea
          disabled={busy}
          maxLength={row.context?.startsWith('user_profile:') ? 2000 : undefined}
          onChange={event => {
            setDraft(previous => ({ ...previous, content: event.target.value }))
            setSaved(false)
          }}
          rows={7}
          value={draft.content}
        />
      </label>
      {!active ? <p className="muted">保存后，伙伴会重新使用这条记忆。</p> : null}
      {conflict ? (
        <div className="creation-status">
          <p>这条记忆已在其他设备修改。请核对最新内容后继续。</p>
          <p className="post-body">{row.content}</p>
          <button
            disabled={busy}
            onClick={() =>
              setDraft(previous => ({ ...previous, baseContent: row.content, version: row.content_version }))
            }
            type="button"
          >
            已核对，保留草稿继续编辑
          </button>
        </div>
      ) : null}
      {error ? (
        <Notice error={error}>
          <p>草稿已保留。请检查最新内容后重试。</p>
          <button disabled={busy} onClick={() => run(onChanged)} type="button">
            刷新最新内容
          </button>
        </Notice>
      ) : null}
      <div className="button-row">
        <button
          className="primary"
          disabled={busy || conflict || !draft.content.trim() || (!dirty && active)}
          onClick={() =>
            run(async () => {
              let updated: MemoryItem
              try {
                updated = await api.invoke('resource_catalog.patch_memory', {
                  path: { memory_id: row.id },
                  body: {
                    system_preset_id: preset,
                    expected_version: draft.version,
                    content: draft.content
                  }
                })
              } catch (failure) {
                if (!alive()) {
                  throw failure
                }
                let latest: MemoryItem | null = null
                try {
                  latest = await findSavedMemory(api, preset, row, draft.content)
                } catch {
                  // 核对失败时保留原草稿与版本。
                }
                if (!latest || latest.content !== draft.content.trim()) {
                  await onChanged()
                  throw failure
                }
                updated = latest
              }
              if (alive()) {
                setDraft({ content: updated.content, baseContent: updated.content, version: updated.content_version })
                setSaved(true)
                await onChanged()
              }
            })
          }
          type="button"
        >
          {busy ? '保存中…' : active ? '保存修改' : '保存并重新记住'}
        </button>
        <button
          disabled={busy}
          onClick={() => {
            setDraft({ content: row.content, baseContent: row.content, version: row.content_version })
            setSaved(false)
          }}
          type="button"
        >
          取消
        </button>
      </div>
      <details className="optional-details">
        <summary>记忆详情</summary>
        {row.reason ? <p className="prompt-copy">{row.reason}</p> : null}
        <p className="muted">
          {row.basis === 'explicit' ? '来自明确表达' : row.basis === 'system' ? '伙伴的生活记录' : '根据对话整理'}
        </p>
        {row.evidence.map(item => (item.quote ? <blockquote key={item.fingerprint}>{item.quote}</blockquote> : null))}
      </details>
      <button
        className="danger"
        disabled={busy}
        onClick={() =>
          run(async () => {
            if (!window.confirm('让伙伴忘记这条记忆？此操作无法撤销。')) {
              return
            }
            await api.invoke('resource_catalog.delete_memory', {
              path: { memory_id: row.id },
              query: { system_preset_id: preset }
            })
            if (alive()) {
              setDraft({ content: '', baseContent: '', version: row.content_version })
              await onChanged()
            }
          })
        }
        type="button"
      >
        忘记这条记忆
      </button>
    </article>
  )
}

interface ProfileDraft {
  values: Partial<Record<UserProfileField, string>>
  base: Partial<Record<UserProfileField, string>>
  versions: Partial<Record<UserProfileField, number>>
}

function UserProfile({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const { run, runLatest, busy, error } = useTask()
  const alive = useAlive()
  const [rows, setRows] = useState<MemoryItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [draft, setDraft] = usePageState<ProfileDraft | null>('memory.profile.draft', null)
  const [selected, setSelected] = usePageState<number | null>('memory.profile.extra', null)
  const [saved, setSaved] = useState(false)
  const current = (context: string) => rows.find(row => row.context === context)
  const changedFields = PROFILE_FIELDS.filter(
    ({ key }) => draft && (draft.values[key] ?? '').trim() !== (draft.base[key] ?? '')
  )
  const conflictingFields = loaded
    ? changedFields.filter(({ key, context }) => (current(context)?.content_version ?? 0) !== draft?.versions[key])
    : []
  const extras = rows.filter(row => !PROFILE_FIELDS.some(field => field.context === row.context))
  const extra = extras.find(row => row.id === selected)

  const load = useCallback(async () => {
    const memories = await readProfile(api)
    if (alive()) {
      setRows(memories)
      setLoaded(true)
    }
  }, [alive, api])

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])
  usePageScroll(`memory.profile.${extra ? selected : 'form'}`, loaded)

  const startDraft = (): ProfileDraft => {
    const values: ProfileDraft['values'] = {}
    const versions: ProfileDraft['versions'] = {}
    PROFILE_FIELDS.forEach(({ key, context }) => {
      values[key] = current(context)?.content ?? ''
      versions[key] = current(context)?.content_version ?? 0
    })

    return { base: { ...values }, values, versions }
  }

  const saveProfile = () =>
    run(async () => {
      if (!draft) {
        return
      }
      const forgotten = changedFields.filter(({ key }) => !draft.values[key]?.trim() && draft.base[key])
      if (
        forgotten.length &&
        !window.confirm(`让伙伴忘记这些资料：${forgotten.map(field => field.label).join('、')}？其他修改会一起保存。`)
      ) {
        return
      }
      const changes = changedFields.map(({ key }) => ({
        field: key,
        value: draft.values[key]?.trim() || null,
        expected_version: draft.versions[key] ?? 0
      }))
      let updated: MemoryItem[]
      try {
        const result = await api.invoke('resource_catalog.patch_memory_profile', {
          body: { system_preset_id: 'companion', changes }
        })
        updated = result.memories
      } catch (failure) {
        if (!alive()) {
          throw failure
        }
        let latest: MemoryItem[] | null = null
        try {
          latest = await readProfile(api)
        } catch {
          // 核对失败不清理草稿，也不改变预期版本。
        }
        if (
          !latest ||
          !changedFields.every(({ key, context }) => {
            const actual = latest.find(row => row.context === context)?.content ?? null

            return actual === (draft.values[key]?.trim() || null)
          })
        ) {
          if (alive() && latest) {
            setRows(latest)
          }
          throw failure
        }
        updated = latest
      }
      if (alive()) {
        setRows(updated)
        setDraft(null)
        setSaved(true)
      }
    })

  return (
    <>
      {error ? (
        <Notice error={error}>
          <p>未保存的修改已保留。</p>
          <button disabled={busy} onClick={() => run(load)} type="button">
            刷新我的资料
          </button>
        </Notice>
      ) : null}
      {extra ? (
        <>
          <button
            className="back-link"
            onClick={() => {
              setSelected(null)
              focusAfterRender(`[data-profile-memory-id="${extra.id}"]`)
            }}
            type="button"
          >
            ← 返回
          </button>
          <MemoryEditor api={api} key={extra.id} onChanged={() => runLatest(load)} preset="companion" row={extra} />
        </>
      ) : (
        <>
          <section className="card profile-editor">
            <h3>我的资料</h3>
            <p className="muted">这些信息帮助伙伴了解你。清空已保存的内容，会让伙伴忘记这一项。</p>
            {changedFields.length ? (
              <p className="draft-label">未保存修改</p>
            ) : saved ? (
              <p aria-live="polite" className="save-status">
                资料已保存
              </p>
            ) : null}
            <div className="edit-fields">
              {PROFILE_FIELDS.map(({ key, context, label, rows: size }) => (
                <label className="field-label" key={key}>
                  {label}
                  <textarea
                    disabled={busy || !loaded}
                    maxLength={2000}
                    onChange={event => {
                      const value = event.target.value
                      setDraft(previous => {
                        const base = previous ?? startDraft()

                        return { ...base, values: { ...base.values, [key]: value } }
                      })
                      setSaved(false)
                    }}
                    rows={size}
                    value={draft ? (draft.values[key] ?? '') : (current(context)?.content ?? '')}
                  />
                </label>
              ))}
            </div>
            {conflictingFields.length ? (
              <div className="creation-status">
                <p>以下资料已在其他设备修改。草稿已保留，请核对最新内容。</p>
                <dl>
                  {conflictingFields.map(({ key, context, label }) => (
                    <div key={key}>
                      <dt>{label}</dt>
                      <dd>{current(context)?.content || '已遗忘'}</dd>
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
                            base: {
                              ...previous.base,
                              ...Object.fromEntries(
                                conflictingFields.map(({ key, context }) => [key, current(context)?.content ?? ''])
                              )
                            },
                            versions: {
                              ...previous.versions,
                              ...Object.fromEntries(
                                conflictingFields.map(({ key, context }) => [
                                  key,
                                  current(context)?.content_version ?? 0
                                ])
                              )
                            }
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
                disabled={busy || !loaded || !changedFields.length || Boolean(conflictingFields.length)}
                onClick={saveProfile}
                type="button"
              >
                {busy ? '保存中…' : '保存修改'}
              </button>
              <button
                disabled={busy || !draft}
                onClick={() => {
                  setDraft(null)
                  setSaved(false)
                }}
                type="button"
              >
                取消
              </button>
            </div>
          </section>
          {extras.length ? (
            <section className="profile-extras">
              <h3>其他资料</h3>
              <div className="memory-summary-list">
                {extras.map(row => (
                  <button
                    className="memory-summary"
                    data-profile-memory-id={row.id}
                    key={row.id}
                    onClick={() => setSelected(row.id)}
                    type="button"
                  >
                    <strong>{row.content}</strong>
                    <span>{memoryTitle(row)}</span>
                    <time dateTime={row.updated_at}>{updatedLabel(row.updated_at)}</time>
                  </button>
                ))}
              </div>
            </section>
          ) : null}
        </>
      )}
    </>
  )
}
