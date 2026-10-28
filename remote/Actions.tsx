import { IconPlus, IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { ActionClipKind, ActionDefinition, ActionLibraryGroup, ActionReviewStatus } from '@protocol'

import {
  ACTION_MOTION_INTENSITIES,
  ACTION_PERFORMANCE_ROLES,
  ACTION_SEMANTIC_TAGS,
  type ActionSemanticTag,
  isActionDesignValid,
  parseActionConditions
} from '../shared/actions'

import { errorText, type RemoteApi } from './api'
import { saveWithReadback } from './save-result'
import {
  Empty,
  focusAfterRender,
  LibraryCard,
  Notice,
  PageHeader,
  useAlive,
  usePageScroll,
  usePageState,
  useTask,
  useVisiblePolling
} from './ui'

const REVIEW_STATUS_TEXT: Record<ActionReviewStatus, string> = {
  pending: '待评审',
  reviewing: '评审中',
  deferred: '待补充资料',
  approved: '已通过',
  rejected: '未通过',
  failed: '评审失败'
}

const CLIP_KIND_TEXT: Record<ActionClipKind, string> = {
  loop: '持续动作',
  once: '短动作'
}

const ROLE_TEXT = { idle: '常驻待机', sustained: '持续状态', reaction: '即时反应' } as const
const INTENSITY_TEXT = { subtle: '轻微', normal: '日常', expressive: '明显' } as const
const TAG_TEXT: Record<ActionSemanticTag, string> = {
  idle: '安静相伴',
  attention: '注意用户',
  interaction: '互动回应',
  reading: '阅读',
  working: '专注做事',
  creating: '创作',
  thinking: '思考',
  resting: '休息',
  organizing: '整理',
  socializing: '社交',
  stretching: '舒展',
  grooming: '整理仪容',
  drinking: '饮水',
  dancing: '舞蹈'
}

interface ActionDraft {
  avoidWhen: string
  clipKind: ActionClipKind
  duration: number
  motion: string
  name: string
  useWhen: string
  semanticTags: ActionSemanticTag[]
  performanceRole: ActionDefinition['performance_role'] | null
  motionIntensity: ActionDefinition['motion_intensity']
  interruptible: boolean
  allowedTriggers: ActionDefinition['allowed_triggers']
  reason: string
  expectedUpdatedAt?: string
}

function toDraft(action: ActionDefinition | null): ActionDraft {
  return {
    avoidWhen: (action?.avoid_when ?? []).join('\n'),
    clipKind: action?.clip_kind ?? 'once',
    duration: action?.duration_seconds ?? 4,
    motion: action?.motion_description ?? '',
    name: action?.name ?? '',
    useWhen: (action?.use_when ?? []).join('\n'),
    semanticTags: [...(action?.semantic_tags ?? [])],
    performanceRole: action?.performance_role ?? null,
    motionIntensity: action?.motion_intensity ?? 'normal',
    interruptible: action?.interruptible ?? true,
    allowedTriggers: action?.allowed_triggers ?? ['chat', 'manual'],
    reason: action?.reason ?? '',
    expectedUpdatedAt: action?.updated_at
  }
}

function actionBody(draft: ActionDraft) {
  return {
    avoid_when: parseActionConditions(draft.avoidWhen),
    clip_kind: draft.clipKind,
    duration_seconds: draft.duration,
    motion_description: draft.motion.trim(),
    name: draft.name.trim(),
    use_when: parseActionConditions(draft.useWhen),
    semantic_tags: draft.semanticTags,
    performance_role: draft.performanceRole ?? (draft.clipKind === 'loop' ? 'sustained' : 'reaction'),
    motion_intensity: draft.motionIntensity,
    interruptible: draft.interruptible,
    allowed_triggers: draft.allowedTriggers,
    reason: draft.reason
  }
}

function ActionEditor({
  action,
  api,
  onChanged,
  copySource,
  onCopy,
  onReload,
  onClose
}: {
  action: ActionDefinition | null
  api: RemoteApi
  onChanged: (saved?: ActionDefinition) => Promise<void>
  onClose: () => void
  copySource: ActionDefinition | null
  onCopy: (source: ActionDefinition) => void
  onReload: (source: ActionDefinition) => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [draft, setDraft] = usePageState<ActionDraft>(
    `action.${action?.id ?? (copySource ? `copy-${copySource.id}` : 'new')}.draft`,
    toDraft(action ?? copySource)
  )
  const patch = (values: Partial<ActionDraft>) => setDraft(previous => ({ ...previous, ...values }))
  const body = actionBody(draft)
  const valid = isActionDesignValid(body)
  const readonly = action?.source === 'preset' || action?.status === 'pending' || action?.status === 'reviewing'
  const changed = JSON.stringify(body) !== JSON.stringify(actionBody(toDraft(action ?? copySource)))

  useEffect(() => {
    if (action && !changed && draft.expectedUpdatedAt !== action.updated_at) {
      setDraft(previous => ({ ...previous, expectedUpdatedAt: action.updated_at }))
    }
  }, [action, changed, draft.expectedUpdatedAt, setDraft])

  return (
    <section className="asset-detail">
      <button className="back-link" onClick={onClose} type="button">
        ← 返回
      </button>
      <div className="card-title">
        <h1>{action ? action.name || '未命名动作' : '新建动作'}</h1>
      </div>
      {action ? (
        <p aria-live="polite" className="muted">
          评审状态：{REVIEW_STATUS_TEXT[action.status]}
          {action.review_reason ? ` · ${action.review_reason}` : ''}
        </p>
      ) : null}
      <fieldset disabled={busy || readonly}>
        <label>
          名称
          <input onChange={event => patch({ name: event.target.value })} value={draft.name} />
        </label>
        <label>
          动作内容
          <textarea
            onChange={event => patch({ motion: event.target.value })}
            placeholder="描述动作的样子，例如：坐在窗边慢慢晃动双腿，偶尔抬头看向窗外"
            rows={4}
            value={draft.motion}
          />
        </label>
        <label>
          适合的情境（每行一条）
          <textarea
            maxLength={2000}
            onChange={event => patch({ useWhen: event.target.value })}
            placeholder="例如：阳光很好的午后"
            rows={3}
            value={draft.useWhen}
          />
        </label>
        <label>
          避免的情境（每行一条）
          <textarea
            maxLength={2000}
            onChange={event => patch({ avoidWhen: event.target.value })}
            placeholder="例如：正在认真聊天的时候"
            rows={3}
            value={draft.avoidWhen}
          />
        </label>
        <p className="muted">每项最多 8 条，每条最多 120 字；留空表示不限。</p>
        <fieldset>
          <legend>播放类型</legend>
          {(['loop', 'once'] as const).map(kind => (
            <label key={kind}>
              <input
                checked={draft.clipKind === kind}
                name="action-clip-kind"
                onChange={() => patch({ clipKind: kind, performanceRole: null })}
                type="radio"
              />
              {CLIP_KIND_TEXT[kind]}
              {kind === 'loop' ? '（循环播放）' : '（播完回到持续画面）'}
            </label>
          ))}
        </fieldset>
        <label>
          时长（秒）
          <input
            max={15}
            min={1}
            onChange={event => patch({ duration: Number(event.target.value) })}
            type="number"
            value={draft.duration}
          />
        </label>
        <fieldset>
          <legend>语义标签（描述动作本身含义，最多 8 个）</legend>
          <div className="tag-row">
            {ACTION_SEMANTIC_TAGS.map(tag => {
              const active = draft.semanticTags.includes(tag)

              return (
                <label key={tag}>
                  <input
                    checked={active}
                    onChange={() =>
                      patch({
                        semanticTags: active
                          ? draft.semanticTags.filter(item => item !== tag)
                          : draft.semanticTags.length >= 8
                            ? draft.semanticTags
                            : [...draft.semanticTags, tag]
                      })
                    }
                    type="checkbox"
                  />
                  {TAG_TEXT[tag]}
                </label>
              )
            })}
          </div>
        </fieldset>
        <fieldset>
          <legend>表现角色（省略按播放类型判断）</legend>
          <label>
            <input
              checked={draft.performanceRole === null}
              name="action-role"
              onChange={() => patch({ performanceRole: null })}
              type="radio"
            />
            自动
          </label>
          {ACTION_PERFORMANCE_ROLES.filter(role => (role === 'reaction') === (draft.clipKind === 'once')).map(role => (
            <label key={role}>
              <input
                checked={draft.performanceRole === role}
                name="action-role"
                onChange={() => patch({ performanceRole: role })}
                type="radio"
              />
              {ROLE_TEXT[role]}
            </label>
          ))}
        </fieldset>
        <fieldset>
          <legend>动作强度（轻量动态下只自动选择轻微动作）</legend>
          {ACTION_MOTION_INTENSITIES.map(level => (
            <label key={level}>
              <input
                checked={draft.motionIntensity === level}
                name="action-intensity"
                onChange={() => patch({ motionIntensity: level })}
                type="radio"
              />
              {INTENSITY_TEXT[level]}
            </label>
          ))}
        </fieldset>
        <label>
          <input
            checked={draft.interruptible}
            onChange={event => patch({ interruptible: event.target.checked })}
            type="checkbox"
          />
          允许互动或新活动中断动作
        </label>
      </fieldset>
      {error ? <Notice>{error}</Notice> : null}
      {!readonly && (
        <div className="creation-footer">
          <button
            className="primary full"
            disabled={busy || !valid}
            onClick={() =>
              run(async () => {
                let saved: ActionDefinition
                if (action) {
                  saved = await saveWithReadback({
                    write: () =>
                      api.invoke('companion_actions.update_action', {
                        path: { action_id: action.id },
                        body: { ...body, expected_updated_at: draft.expectedUpdatedAt ?? action.updated_at }
                      }),
                    read: () => api.invoke('companion_actions.get_action', { path: { action_id: action.id } }),
                    matches: latest => JSON.stringify(actionBody(toDraft(latest))) === JSON.stringify(body),
                    isCurrent: alive,
                    onUnconfirmed: latest => {
                      if (latest) {
                        onReload(latest)
                      }
                    }
                  })
                } else {
                  saved = await api.invoke('companion_actions.create_action', {
                    body
                  })
                }

                if (alive()) {
                  setDraft(toDraft(action ? saved : copySource))
                  onClose()
                  await onChanged(saved)
                }
              })
            }
            type="button"
          >
            {uiSaveLabel(action)}
          </button>
        </div>
      )}
      {action?.source === 'preset' && (
        <button className="primary" onClick={() => onCopy(action)} type="button">
          复制后创作
        </button>
      )}
      {action && (action.status === 'failed' || action.status === 'deferred') && (
        <button
          disabled={busy || changed}
          onClick={() =>
            run(async () => {
              const saved = await saveWithReadback({
                write: () =>
                  api.invoke('companion_actions.retry_review', {
                    path: { action_id: action.id },
                    body: { expected_updated_at: action.updated_at }
                  }),
                read: () => api.invoke('companion_actions.get_action', { path: { action_id: action.id } }),
                matches: latest =>
                  latest.updated_at !== action.updated_at &&
                  JSON.stringify(actionBody(toDraft(latest))) === JSON.stringify(actionBody(toDraft(action))) &&
                  JSON.stringify(latest.allowed_triggers) === JSON.stringify(action.allowed_triggers) &&
                  latest.reason === action.reason,
                isCurrent: alive,
                onUnconfirmed: latest => {
                  if (latest) {
                    onReload(latest)
                  }
                }
              })
              if (alive()) {
                onClose()
                await onChanged(saved)
              }
            })
          }
          type="button"
        >
          重试评审
        </button>
      )}
      {action && action.source !== 'preset' ? (
        <details className="optional-details">
          <summary>更多操作</summary>
          <div className="button-row">
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  if (changed && !window.confirm('重新载入会放弃当前未保存的修改，继续吗？')) {
                    return
                  }
                  const latest = await api.invoke('companion_actions.get_action', { path: { action_id: action.id } })
                  if (alive()) {
                    setDraft(toDraft(latest))
                    onReload(latest)
                  }
                })
              }
              type="button"
            >
              重新载入已保存内容
            </button>
            <button
              className="danger"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  if (!window.confirm('删除这个动作定义？删除后无法恢复。')) {
                    return
                  }
                  await api.invoke('companion_actions.delete_action', { path: { action_id: action.id } })

                  if (alive()) {
                    onClose()
                    await onChanged()
                  }
                })
              }
              type="button"
            >
              删除
            </button>
          </div>
        </details>
      ) : null}
    </section>
  )
}

function uiSaveLabel(action: ActionDefinition | null): string {
  return action ? '保存修改' : '创建动作'
}

export function Actions({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const alive = useAlive()
  const { run, runLatest, busy, error } = useTask()
  const [rows, setRows] = useState<ActionDefinition[]>([])
  const [hasMore, setHasMore] = useState(false)
  const nextCursor = useRef<Record<ActionLibraryGroup, string | null>>({ in_library: null, not_in_library: null })
  const cache = useRef<Record<ActionLibraryGroup, { rows: ActionDefinition[]; query: string }>>({
    in_library: { rows: [], query: '' },
    not_in_library: { rows: [], query: '' }
  })
  const [group, setGroup] = usePageState<ActionLibraryGroup>('actions.group', 'in_library')
  const [copySource, setCopySource] = usePageState<ActionDefinition | null>('actions.copySource', null)
  const [selected, setSelected] = usePageState<string | null>('actions.selected', null)
  const [creating, setCreating] = usePageState('actions.creating', false)
  const [query, setQuery] = usePageState('actions.query', '')
  const [loaded, setLoaded] = useState(false)
  const revision = useRef(0)
  const editingRef = useRef<ActionDefinition | null>(null)
  const [editing, setEditing] = useState<ActionDefinition | null>(null)
  const [detailError, setDetailError] = useState('')
  usePageScroll(`actions.${creating ? 'create' : (selected ?? `list.${group}.${query}`)}`, loaded)

  useEffect(() => {
    editingRef.current = editing
  }, [editing])

  const load = useCallback(
    async (append = false) => {
      const version = ++revision.current
      const sameQuery = query === cache.current[group].query
      const previousCursor = nextCursor.current[group]
      if (!append) {
        // 刷新期间先停用旧游标；同一查询失败时恢复它。
        nextCursor.current[group] = null
      }
      try {
        const list = await api.invoke('companion_actions.list_actions', {
          query: {
            query: query.trim() || undefined,
            group,
            cursor: append ? (nextCursor.current[group] ?? undefined) : undefined,
            limit: 50
          }
        })

        if (alive() && revision.current === version) {
          const previous = cache.current[group].rows
          const updated = append
            ? [...previous, ...list.actions.filter(row => !previous.some(old => old.id === row.id))]
            : list.actions
          cache.current[group] = { rows: updated, query }
          setRows(updated)
          setHasMore(list.has_more)
          nextCursor.current[group] = list.next_cursor
          setLoaded(true)
        }
      } catch (error) {
        if (alive() && revision.current === version && !append && sameQuery) {
          nextCursor.current[group] = previousCursor
        }
        throw error
      }
    },
    [alive, api, group, query]
  )

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])

  // 语义定义的登记与评审由服务端推进，页面按需轻量轮询评审状态。
  useVisiblePolling(
    rows.some(row => row.status === 'pending' || row.status === 'reviewing'),
    load,
    runLatest
  )

  const visible = rows

  useEffect(() => {
    if (!selected) {
      setEditing(null)
      return
    }
    setDetailError('')
    const listed = rows.find(row => row.id === selected)
    const currentEditing = editingRef.current
    if (
      currentEditing?.id === selected &&
      listed?.updated_at === currentEditing.updated_at &&
      listed.status === currentEditing.status
    ) {
      return
    }
    let current = true
    void api
      .invoke('companion_actions.get_action', { path: { action_id: selected } })
      .then(action => {
        if (current && alive()) {
          setEditing(action)
        }
      })
      .catch(failure => {
        if (current && alive()) {
          setDetailError(errorText(failure))
          setSelected(null)
        }
      })
    return () => {
      current = false
    }
  }, [alive, api, selected, setSelected, refresh, rows])

  return (
    <>
      {creating || editing ? (
        <ActionEditor
          action={editing}
          api={api}
          copySource={copySource}
          key={editing?.id ?? (copySource ? `copy-${copySource.id}` : 'new')}
          onChanged={async saved => {
            const target = saved ? (saved.status === 'approved' ? 'in_library' : 'not_in_library') : group
            if (target === group) {
              await runLatest(load)
            } else {
              revision.current++
              setRows([])
              setLoaded(false)
              setHasMore(false)
              setGroup(target)
            }
          }}
          onClose={() => {
            setCreating(false)
            setSelected(null)
            setEditing(null)
            focusAfterRender(creating ? '[data-create-asset="action"]' : `[data-card-key="action-${selected}"]`)
          }}
          onCopy={source => {
            setCopySource(source)
            setCreating(true)
            setSelected(null)
            setEditing(null)
          }}
          onReload={setEditing}
        />
      ) : (
        <>
          <PageHeader subtitle="已入库动作可用于制作影像；未入库动作可查看评审进度、修改或重试。" title="动作" />
          <nav aria-label="动作分组" className="section-tabs">
            {(
              [
                { id: 'in_library', label: '已入库' },
                { id: 'not_in_library', label: '未入库' }
              ] as const
            ).map(item => (
              <button
                aria-current={group === item.id ? 'page' : undefined}
                className={group === item.id ? 'selected' : ''}
                key={item.id}
                onClick={() => {
                  if (group === item.id) {
                    return
                  }
                  revision.current++
                  const saved = cache.current[item.id]
                  const matching = saved.query === query
                  setRows(matching ? saved.rows : [])
                  setHasMore(matching && Boolean(nextCursor.current[item.id]))
                  setLoaded(false)
                  setGroup(item.id)
                }}
                type="button"
              >
                {item.label}
              </button>
            ))}
          </nav>
          <div className="library-toolbar">
            <input
              aria-label="搜索动作"
              onChange={event => {
                revision.current++
                setRows([])
                setHasMore(false)
                setLoaded(false)
                setQuery(event.target.value)
              }}
              placeholder="搜索动作"
              type="search"
              value={query}
            />
            <button
              aria-label="新建动作"
              className="primary"
              data-create-asset="action"
              onClick={() => {
                setCreating(true)
                setCopySource(null)
                setSelected(null)
                setEditing(null)
              }}
              type="button"
            >
              <IconPlus aria-hidden size={17} />
              新建动作
            </button>
            <button
              aria-label="刷新动作"
              className="icon-button"
              disabled={busy}
              onClick={() => run(load)}
              type="button"
            >
              <IconRefresh size={20} />
            </button>
          </div>
          {error || detailError ? <Notice>{error || detailError}</Notice> : null}
          <div className="asset-grid">
            {visible.map(row => (
              <LibraryCard
                cardKey={`action-${row.id}`}
                imageAlt={row.name}
                imageUrl=""
                key={row.id}
                meta={`${CLIP_KIND_TEXT[row.clip_kind]} · ${row.duration_seconds} 秒 · ${row.source === 'preset' ? '内置' : '自创'}`}
                onSelect={() => {
                  setCreating(false)
                  setSelected(row.id)
                  setEditing(row)
                }}
                submeta={
                  group === 'not_in_library'
                    ? `${REVIEW_STATUS_TEXT[row.status]}${row.review_reason ? ` · ${row.review_reason}` : ''}`
                    : undefined
                }
                title={row.name || '未命名动作'}
              />
            ))}
          </div>
          {hasMore ? (
            <button className="load-more" disabled={busy} onClick={() => void run(() => load(true))} type="button">
              更多动作
            </button>
          ) : null}
          {loaded && visible.length === 0 && !busy ? (
            <Empty title={query ? '没有找到匹配动作' : group === 'in_library' ? '还没有已入库动作' : '没有未入库动作'}>
              自创动作通过评审后会加入动作库。
            </Empty>
          ) : null}
        </>
      )}
    </>
  )
}
