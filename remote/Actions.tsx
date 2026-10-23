import { IconPlus, IconRefresh } from '@tabler/icons-react'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { ActionClipKind, ActionDefinition, ActionReviewStatus } from '@protocol'

import type { RemoteApi } from './api'
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
  deferred: '暂缓',
  approved: '已通过',
  rejected: '未通过'
}

const CLIP_KIND_TEXT: Record<ActionClipKind, string> = {
  loop: '持续动作',
  once: '短动作'
}

interface ActionDraft {
  avoidWhen: string
  clipKind: ActionClipKind
  duration: number
  motion: string
  name: string
  useWhen: string
}

function toLines(value: string): string[] {
  return value
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
}

function toDraft(action: ActionDefinition | null): ActionDraft {
  return {
    avoidWhen: (action?.avoid_when ?? []).join('\n'),
    clipKind: action?.clip_kind ?? 'loop',
    duration: action?.duration_seconds ?? 5,
    motion: action?.motion_description ?? '',
    name: action?.name ?? '',
    useWhen: (action?.use_when ?? []).join('\n')
  }
}

function ActionEditor({
  action,
  api,
  onChanged,
  onClose
}: {
  action: ActionDefinition | null
  api: RemoteApi
  onChanged: () => Promise<void>
  onClose: () => void
}): ReactElement {
  const { run, busy, error } = useTask()
  const alive = useAlive()
  const [draft, setDraft] = usePageState<ActionDraft>(`action.${action?.id ?? 'new'}.draft`, toDraft(action))
  const patch = (values: Partial<ActionDraft>) => setDraft(previous => ({ ...previous, ...values }))
  const valid = Boolean(draft.name.trim() && draft.motion.trim() && draft.duration >= 1 && draft.duration <= 60)

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
      <label>
        名称
        <input maxLength={64} onChange={event => patch({ name: event.target.value })} value={draft.name} />
      </label>
      <label>
        动作内容
        <textarea
          maxLength={2000}
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
      <fieldset>
        <legend>播放类型</legend>
        {(['loop', 'once'] as const).map(kind => (
          <label key={kind}>
            <input
              checked={draft.clipKind === kind}
              name="action-clip-kind"
              onChange={() => patch({ clipKind: kind })}
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
          max={60}
          min={1}
          onChange={event => patch({ duration: Number(event.target.value) })}
          type="number"
          value={draft.duration}
        />
      </label>
      {error ? <Notice>{error}</Notice> : null}
      <div className="creation-footer">
        <button
          className="primary full"
          disabled={busy || !valid}
          onClick={() =>
            run(async () => {
              const body = {
                avoid_when: toLines(draft.avoidWhen),
                clip_kind: draft.clipKind,
                duration_seconds: draft.duration,
                motion_description: draft.motion.trim(),
                name: draft.name.trim(),
                use_when: toLines(draft.useWhen)
              }
              if (action) {
                await api.invoke('companion_actions.update_action', {
                  path: { action_id: action.id },
                  body: { ...body, expected_updated_at: action.updated_at }
                })
              } else {
                await api.invoke('companion_actions.create_action', { body })
              }

              if (alive()) {
                onClose()
              }

              await onChanged()
            })
          }
          type="button"
        >
          {uiSaveLabel(action)}
        </button>
      </div>
      {action ? (
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
                  }

                  await onChanged()
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
  const [selected, setSelected] = usePageState<number | null>('actions.selected', null)
  const [creating, setCreating] = usePageState('actions.creating', false)
  const [query, setQuery] = usePageState('actions.query', '')
  const [loaded, setLoaded] = useState(false)
  const revision = useRef(0)
  const editing = rows.find(row => row.id === selected) ?? null
  usePageScroll(`actions.${creating ? 'create' : (selected ?? `list.${query}`)}`, loaded)

  const load = useCallback(async () => {
    const version = ++revision.current
    const list = await api.invoke('companion_actions.list_actions', {})

    if (alive() && revision.current === version) {
      setRows(list.actions)
      setLoaded(true)
    }
  }, [alive, api])

  useEffect(() => {
    void runLatest(load)
  }, [load, refresh, runLatest])

  // 语义定义的登记与评审由服务端推进，页面按需轻量轮询评审状态。
  useVisiblePolling(
    rows.some(row => row.status === 'pending' || row.status === 'reviewing'),
    load,
    runLatest
  )

  const visible = query.trim()
    ? rows.filter(row => row.name.includes(query.trim()) || row.motion_description.includes(query.trim()))
    : rows

  return (
    <>
      {creating || editing ? (
        <ActionEditor
          action={editing}
          api={api}
          onChanged={load}
          onClose={() => {
            setCreating(false)
            setSelected(null)
            focusAfterRender(creating ? '[data-create-asset="action"]' : `[data-card-key="action-${selected}"]`)
          }}
        />
      ) : (
        <>
          <PageHeader subtitle="定义伙伴可以做出的动作语义，供影像组合使用" title="动作" />
          <div className="library-toolbar">
            <input
              aria-label="搜索动作"
              onChange={event => {
                revision.current++
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
          {error ? <Notice>{error}</Notice> : null}
          <div className="asset-grid">
            {visible.map(row => (
              <LibraryCard
                cardKey={`action-${row.id}`}
                imageAlt={row.name}
                imageUrl=""
                key={row.id}
                meta={`${CLIP_KIND_TEXT[row.clip_kind]} · ${row.duration_seconds} 秒 · ${REVIEW_STATUS_TEXT[row.status]}`}
                onSelect={() => {
                  setCreating(false)
                  setSelected(row.id)
                }}
                title={row.name || '未命名动作'}
              />
            ))}
          </div>
          {loaded && visible.length === 0 && !busy ? (
            <Empty title="还没有动作定义">新建动作语义，影像组合时可以派上用场。</Empty>
          ) : null}
        </>
      )}
    </>
  )
}
