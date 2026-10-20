import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { MemoryCounts, MemoryItem, SystemPresetSummary, UserProfileField } from '@protocol'

import type { RemoteApi } from './api'
import type { MemoryDraft, MemoryDrafts } from './memory-state'
import {
  acceptMemoryConflict,
  clearMemoryDraft,
  forgetMemory,
  isMemoryConflict,
  readActiveMemories,
  readMemoryDraft,
  saveMemoryDraft,
  storeMemoryDraft,
  writeMemoryDraft
} from './memory-state'
import { saveWithReadback } from './save-result'
import { Empty, Notice, useAlive, usePageScroll, usePageState, useTask } from './ui'

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

const BASIS_LABELS: Record<MemoryItem['basis'], string> = {
  explicit: '来自明确表达',
  inferred: '根据对话整理',
  observed: '来自事件观察',
  system: '伙伴的生活记录'
}

const USAGE_LABELS: Record<MemoryItem['usage'], string> = {
  contextual: '按需使用',
  background: '长期背景'
}

const STANCE_LABELS: Record<'supports' | 'opposes', string> = {
  supports: '支持依据',
  opposes: '相反依据'
}

function memoryTitle(row: MemoryItem): string {
  return row.context?.replace(/^(recall|user_profile):/, '').replaceAll('_', ' ') || '记忆'
}

function updatedLabel(value: string): string {
  const date = new Date(value)

  return Number.isFinite(date.getTime())
    ? date.toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })
    : value
}

function memoryTags(row: MemoryItem): string[] {
  try {
    const parsed: unknown = JSON.parse(row.tags || '[]')

    return Array.isArray(parsed) ? parsed.map(String) : []
  } catch {
    return []
  }
}

function isActiveMemory(row: MemoryItem): boolean {
  return row.status === 'active' && (!row.expires_at || Date.parse(row.expires_at) > Date.now())
}

async function readProfile(api: RemoteApi): Promise<MemoryItem[]> {
  const memories: MemoryItem[] = []
  for await (const page of readActiveMemories(api, { system_preset_id: 'companion', kind: 'user_profile' })) {
    memories.push(...page)
  }

  return memories
}

/** 展开后把卡片顶部对齐到可视区，并聚焦编辑框。 */
function focusMemoryEditor(id: number): void {
  requestAnimationFrame(() => {
    const content = document.querySelector<HTMLElement>('.content')
    const card = content?.querySelector<HTMLElement>(`[data-memory-id="${id}"]`)

    if (content && card) {
      content.scrollTop += card.getBoundingClientRect().top - content.getBoundingClientRect().top
    }

    content?.querySelector<HTMLTextAreaElement>(`#memory-${id}`)?.focus({ preventScroll: true })
  })
}

/** 收起后焦点回到该条记忆的卡片。 */
function focusMemoryCard(id: number): void {
  requestAnimationFrame(() => {
    const content = document.querySelector<HTMLElement>('.content')

    content?.querySelector<HTMLElement>(`[data-memory-id="${id}"] .memory-summary`)?.focus({ preventScroll: true })
  })
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
  const [expanded, setExpanded] = usePageState<number | null>(`memory.${preset}.expanded`, null)
  const [drafts, setDrafts] = usePageState<MemoryDrafts>(`memory.${preset}.drafts`, {})
  const request = useRef(0)
  const expandedRef = useRef(expanded)
  expandedRef.current = expanded

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
      if (expandedRef.current !== null && !result.memories.some(row => row.id === expandedRef.current)) {
        setExpanded(null)
      }
    }
  }, [alive, api, offset, preset, query, setExpanded, setOffset, status])

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])
  usePageScroll(`memory.${preset}.${status}.${offset}.${query}`, loaded)

  const collapse = (focusCard: boolean) => {
    const id = expandedRef.current
    setExpanded(null)
    if (focusCard && id !== null) {
      focusMemoryCard(id)
    }
  }

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
          setExpanded(null)
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
              setExpanded(null)
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
      <div className="memory-list">
        {rows.map(row => {
          const draft = readMemoryDraft(drafts, row)
          const open = expanded === row.id

          return (
            <article className={`memory-item ${open ? 'expanded' : ''}`} data-memory-id={row.id} key={row.id}>
              {open ? (
                <MemoryEditor
                  api={api}
                  draft={draft}
                  drafts={drafts}
                  onChanged={() => runLatest(load)}
                  onCollapse={collapse}
                  onDraftsChange={setDrafts}
                  preset={preset}
                  row={row}
                />
              ) : (
                <button
                  aria-controls={`memory-editor-${row.id}`}
                  aria-expanded={false}
                  className="memory-summary"
                  onClick={() => {
                    setExpanded(row.id)
                    focusMemoryEditor(row.id)
                  }}
                  type="button"
                >
                  <strong>{row.content}</strong>
                  <span>{memoryTitle(row)}</span>
                  <time dateTime={row.updated_at}>{updatedLabel(row.updated_at)}</time>
                  {draft ? <span className="draft-label">未保存修改</span> : null}
                </button>
              )}
            </article>
          )
        })}
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
            setExpanded(null)
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
            setExpanded(null)
          }}
          type="button"
        >
          下一页
        </button>
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
  draft,
  drafts,
  onChanged,
  onCollapse,
  onDraftsChange
}: {
  api: RemoteApi
  row: MemoryItem
  preset: string
  draft: MemoryDraft | undefined
  drafts: MemoryDrafts
  onChanged: () => Promise<void>
  onCollapse: (focusCard: boolean) => void
  onDraftsChange: (drafts: MemoryDrafts) => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [saved, setSaved] = useState(false)
  const active = isActiveMemory(row)
  const conflict = isMemoryConflict(row, draft)
  const edit: MemoryDraft = draft ?? {
    baseContent: row.content,
    content: row.content,
    version: row.content_version
  }

  const save = () =>
    run(async () => {
      await saveMemoryDraft(api, {
        preset,
        row,
        draft: edit,
        isCurrent: alive,
        onUnconfirmed: () => onChanged()
      })
      if (alive()) {
        onDraftsChange(clearMemoryDraft(drafts, row))
        setSaved(true)
        await onChanged()
      }
    })

  const forget = () =>
    run(async () => {
      if (!window.confirm('让伙伴忘记这条记忆？此操作无法撤销。')) {
        return
      }
      await forgetMemory(api, preset, row)
      if (alive()) {
        onDraftsChange(clearMemoryDraft(drafts, row))
        onCollapse(false)
        await onChanged()
      }
    })

  return (
    <div className="memory-editor" id={`memory-editor-${row.id}`}>
      <div className="memory-editor-header">
        <p className="eyebrow">{memoryTitle(row)}</p>
        <div className="memory-editor-actions">
          {draft ? (
            <span className="draft-label">未保存修改</span>
          ) : saved ? (
            <p aria-live="polite" className="save-status">
              修改已保存
            </p>
          ) : null}
          <button className="memory-collapse" onClick={() => onCollapse(true)} type="button">
            收起
          </button>
        </div>
      </div>
      <label className="field-label">
        记忆内容
        <textarea
          disabled={busy}
          id={`memory-${row.id}`}
          maxLength={row.context?.startsWith('user_profile:') ? 2000 : undefined}
          onChange={event => {
            setSaved(false)
            onDraftsChange(writeMemoryDraft(drafts, row, event.target.value))
          }}
          rows={7}
          value={edit.content}
        />
      </label>
      {!active ? <p className="muted">保存后，伙伴会重新使用这条记忆。</p> : null}
      {conflict && draft ? (
        <div className="creation-status">
          <p>这条记忆已在其他设备修改。请核对最新内容后继续。</p>
          <p className="post-body">{row.content}</p>
          <button
            disabled={busy}
            onClick={() => onDraftsChange(storeMemoryDraft(drafts, row, acceptMemoryConflict(row, draft)))}
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
          disabled={busy || conflict || !edit.content.trim() || (active && !draft)}
          onClick={save}
          type="button"
        >
          {busy ? '保存中…' : active ? '保存修改' : '保存并重新记住'}
        </button>
        <button
          disabled={busy || !draft}
          onClick={() => {
            setSaved(false)
            onDraftsChange(clearMemoryDraft(drafts, row))
          }}
          type="button"
        >
          取消
        </button>
        <button className="danger" disabled={busy} onClick={forget} type="button">
          忘记这条记忆
        </button>
      </div>
      <p className="muted">
        {memoryTitle(row)} · 更新于 {updatedLabel(row.updated_at)}
      </p>
      <MemoryDetails row={row} />
    </div>
  )
}

function MemoryDetails({ row }: { row: MemoryItem }): ReactElement {
  const tags = memoryTags(row)

  return (
    <details className="optional-details">
      <summary>记忆详情</summary>
      <p className="muted">
        {BASIS_LABELS[row.basis]} · {USAGE_LABELS[row.usage]}
      </p>
      {row.reason ? <p className="prompt-copy">{row.reason}</p> : null}
      {row.expires_at ? <p className="muted">有效期至 {row.expires_at}</p> : null}
      {tags.length ? (
        <div className="memory-tags">
          {tags.map(tag => (
            <span className="pill" key={tag}>
              {tag}
            </span>
          ))}
        </div>
      ) : null}
      {row.evidence.map((item, index) => (
        <blockquote key={`${item.fingerprint}-${index}`}>
          {item.stance ? `${STANCE_LABELS[item.stance]} · ${item.created_at ?? ''}` : null}
          {item.quote}
        </blockquote>
      ))}
    </details>
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
  const [drafts, setDrafts] = usePageState<MemoryDrafts>('memory.companion.drafts', {})
  const [saved, setSaved] = useState(false)
  const current = (context: string) => rows.find(row => row.context === context)
  const changedFields = PROFILE_FIELDS.filter(
    ({ key }) => draft && (draft.values[key] ?? '').trim() !== (draft.base[key] ?? '')
  )
  const conflictingFields = loaded
    ? changedFields.filter(({ key, context }) => (current(context)?.content_version ?? 0) !== draft?.versions[key])
    : []
  const extras = rows.filter(row => !PROFILE_FIELDS.some(field => field.context === row.context))

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
  usePageScroll('memory.profile.form', loaded)

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
      const updated = await saveWithReadback({
        write: async () => {
          const result = await api.invoke('resource_catalog.patch_memory_profile', {
            body: { system_preset_id: 'companion', changes }
          })

          return result.memories
        },
        read: () => readProfile(api),
        matches: latest =>
          changedFields.every(({ key, context }) => {
            const actual = latest.find(row => row.context === context)?.content ?? null

            return actual === (draft.values[key]?.trim() || null)
          }),
        isCurrent: alive,
        onUnconfirmed: latest => {
          if (latest) {
            setRows(latest)
          }
        }
      })
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
                            conflictingFields.map(({ key, context }) => [key, current(context)?.content_version ?? 0])
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
          {extras.map(row => (
            <ProfileExtraCard
              api={api}
              draft={readMemoryDraft(drafts, row)}
              drafts={drafts}
              key={row.id}
              onChanged={() => runLatest(load)}
              onDraftsChange={setDrafts}
              row={row}
            />
          ))}
        </section>
      ) : null}
    </>
  )
}

function ProfileExtraCard({
  api,
  row,
  draft,
  drafts,
  onChanged,
  onDraftsChange
}: {
  api: RemoteApi
  row: MemoryItem
  draft: MemoryDraft | undefined
  drafts: MemoryDrafts
  onChanged: () => Promise<void>
  onDraftsChange: (drafts: MemoryDrafts) => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [saved, setSaved] = useState(false)
  const conflict = isMemoryConflict(row, draft)
  const edit: MemoryDraft = draft ?? {
    baseContent: row.content,
    content: row.content,
    version: row.content_version
  }

  const save = () =>
    run(async () => {
      await saveMemoryDraft(api, {
        preset: 'companion',
        row,
        draft: edit,
        isCurrent: alive,
        onUnconfirmed: () => onChanged()
      })
      if (alive()) {
        onDraftsChange(clearMemoryDraft(drafts, row))
        setSaved(true)
        await onChanged()
      }
    })

  const forget = () =>
    run(async () => {
      if (!window.confirm('让伙伴忘记这条资料？此操作无法撤销。')) {
        return
      }
      await forgetMemory(api, 'companion', row)
      if (alive()) {
        onDraftsChange(clearMemoryDraft(drafts, row))
        await onChanged()
      }
    })

  return (
    <article className="memory-item expanded">
      <div className="memory-editor">
        <div className="memory-editor-header">
          <p className="eyebrow">{memoryTitle(row)}</p>
          <div className="memory-editor-actions">
            {draft ? (
              <span className="draft-label">未保存修改</span>
            ) : saved ? (
              <p aria-live="polite" className="save-status">
                资料已保存
              </p>
            ) : null}
          </div>
        </div>
        <label className="field-label">
          资料内容
          <textarea
            disabled={busy}
            id={`profile-memory-${row.id}`}
            maxLength={2000}
            onChange={event => {
              setSaved(false)
              onDraftsChange(writeMemoryDraft(drafts, row, event.target.value))
            }}
            rows={2}
            value={edit.content}
          />
        </label>
        {conflict && draft ? (
          <div className="creation-status">
            <p>这条资料已在其他设备修改。请核对最新内容后继续。</p>
            <p className="post-body">{row.content}</p>
            <button
              disabled={busy}
              onClick={() => onDraftsChange(storeMemoryDraft(drafts, row, acceptMemoryConflict(row, draft)))}
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
            disabled={busy || conflict || !edit.content.trim() || !draft}
            onClick={save}
            type="button"
          >
            {busy ? '保存中…' : '保存修改'}
          </button>
          <button
            disabled={busy || !draft}
            onClick={() => {
              setSaved(false)
              onDraftsChange(clearMemoryDraft(drafts, row))
            }}
            type="button"
          >
            取消
          </button>
          <button className="danger" disabled={busy} onClick={forget} type="button">
            忘记这条资料
          </button>
        </div>
        <MemoryDetails row={row} />
      </div>
    </article>
  )
}
