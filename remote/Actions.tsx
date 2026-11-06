import { IconPlus, IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { ActionClipKind, ActionDefinition, ActionLibraryGroup, ActionReviewStatus } from '@protocol'

import { composeDescribedInput } from '../shared/described-input'
import { InterpretationGuard } from '../shared/interpretation-guard'
import { isRequestConflictError } from '../shared/visuals'

import { ApiError, errorText, type RemoteApi } from './api'
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

interface ActionComposer {
  description: string
  refinement: string
  preview: { draft: Record<string, unknown>; summary: string; details: { label: string; value: string }[] } | null
  expectedUpdatedAt: string | null
  requestId: string | null
}

const EMPTY_COMPOSER: ActionComposer = {
  description: '',
  refinement: '',
  preview: null,
  expectedUpdatedAt: null,
  requestId: null
}

const interpretation = new InterpretationGuard()

/** 动作编辑：新建与修改都从一句描述开始，先看整理结果再保存并评审。 */
function ActionEditor({
  action,
  api,
  onChanged,
  onReload,
  onClose
}: {
  action: ActionDefinition | null
  api: RemoteApi
  onChanged: (saved?: ActionDefinition) => Promise<void>
  onClose: () => void
  onReload: (source: ActionDefinition) => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [composer, setComposer] = usePageState<ActionComposer>(`action.${action?.id ?? 'new'}.composer`, EMPTY_COMPOSER)
  const patch = (values: Partial<ActionComposer>): void => {
    // 草稿任何变化都推进整理修订，在途整理的响应不再落地。
    interpretation.revise()
    setComposer(previous => ({ ...previous, ...values }))
  }
  const readonly = action?.source === 'preset' || action?.status === 'pending' || action?.status === 'reviewing'
  const description = composeDescribedInput(composer.description, composer.refinement)

  const interpret = (): Promise<void> =>
    run(async () => {
      if (!description) {
        return
      }

      // 结果只在请求代次与草稿修订都未变时落地：改描述、换动作或关编辑器都会作废在途整理。
      const accept = interpretation.begin()

      try {
        const result = await api.invoke('companion_actions.interpret_action', {
          body: { description, action_id: action?.id ?? null }
        })

        if (alive() && accept()) {
          patch({
            description,
            refinement: '',
            requestId: null,
            preview: { draft: result.draft, summary: result.summary, details: result.details },
            expectedUpdatedAt: result.expected_updated_at ?? composer.expectedUpdatedAt
          })
        }
      } catch (failure) {
        // 过期失败不替换当前提示，也不清草稿。
        if (alive() && accept()) {
          throw failure
        }
      }
    })

  const save = (): Promise<void> =>
    run(async () => {
      const plan = composer.preview

      if (!plan) {
        return
      }

      const request_id = composer.requestId ?? crypto.randomUUID()
      patch({ requestId: request_id })
      let saved: ActionDefinition

      if (action) {
        saved = await saveWithReadback({
          write: () =>
            api.invoke('companion_actions.update_action', {
              path: { action_id: action.id },
              body: { design: plan.draft, expected_updated_at: composer.expectedUpdatedAt ?? action.updated_at }
            }),
          read: () => api.invoke('companion_actions.get_action', { path: { action_id: action.id } }),
          matches: latest =>
            latest.name === String(plan.draft.name ?? '') &&
            latest.motion_description === String(plan.draft.motion_description ?? ''),
          isCurrent: alive,
          onUnconfirmed: latest => {
            if (latest) {
              onReload(latest)
            }
          }
        })
      } else {
        try {
          saved = await api.invoke('companion_actions.create_action', {
            body: { request_id, design: plan.draft }
          })
        } catch (failure) {
          // 请求身份已绑定另一份设计：换新身份再试，不能沿用旧请求。
          if (alive() && failure instanceof ApiError && isRequestConflictError(failure.code)) {
            patch({ requestId: null })
          }

          throw failure
        }
      }

      if (alive()) {
        patch({ requestId: null })
        onClose()
        await onChanged(saved)
      }
    })

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
      {!composer.preview ? (
        <label>
          {action ? '想改哪里' : '它会在什么时候、大概怎么动'}
          <textarea
            disabled={busy || readonly}
            onChange={event => patch({ description: event.target.value })}
            placeholder="例如：阳光很好的午后，坐在窗边慢慢晃动双腿，偶尔抬头看向窗外"
            rows={4}
            value={composer.description}
          />
        </label>
      ) : (
        <div aria-live="polite">
          <h2>这样理解对吗</h2>
          <p>
            <strong>{composer.preview.summary}</strong>
          </p>
          <dl>
            {composer.preview.details.map(item => (
              <div key={`${item.label}:${item.value}`}>
                <dt>{item.label}</dt>
                <dd>{item.value}</dd>
              </div>
            ))}
          </dl>
          <label>
            还需要补充什么
            <input
              disabled={busy || readonly}
              onChange={event => patch({ refinement: event.target.value })}
              placeholder="例如：幅度再小一些"
              value={composer.refinement}
            />
          </label>
        </div>
      )}
      {error ? <Notice>{error}</Notice> : null}
      {!readonly && (
        <div className="creation-footer">
          <button
            className="primary full"
            disabled={busy || (!composer.preview && !description)}
            onClick={() => (composer.preview ? save() : interpret())}
            type="button"
          >
            {composer.preview ? '保存并评审' : '整理描述'}
          </button>
          {composer.preview && (
            <button disabled={busy} onClick={() => void interpret()} type="button">
              再说一句
            </button>
          )}
        </div>
      )}
      {action && (action.status === 'failed' || action.status === 'deferred') && (
        <button
          disabled={busy}
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
                  latest.name === action.name &&
                  latest.motion_description === action.motion_description,
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
  // 详情与创建视图随组件生命周期，切 tab 即回列表；筛选与编辑草稿保留在 usePageState。
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
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
            cursor: append ? (nextCursor.current[group] ?? undefined) : undefined
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
          key={editing?.id ?? 'new'}
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
            {rows.map(row => (
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
          {loaded && rows.length === 0 && !busy ? (
            <Empty title={query ? '没有找到匹配动作' : group === 'in_library' ? '还没有已入库动作' : '没有未入库动作'}>
              自创动作通过评审后会加入动作库。
            </Empty>
          ) : null}
        </>
      )}
    </>
  )
}
