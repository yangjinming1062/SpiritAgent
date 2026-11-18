import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { ApiSchemas } from '../shared/api.generated'
import {
  archiveSearchCopy,
  type ArchiveSearchFilters,
  archiveSearchQuery,
  emptyArchiveSearch
} from '../shared/archive-search'
import { lifeStepStatus } from '../shared/life-trajectory'

import type { RemoteApi } from './api'
import { ArchiveSearchPanel } from './ArchiveSearch'
import { LifeEpisodeCard, LifeReading, LifeResultPreviews, LifeTimeline } from './LifeTrajectory'
import { Notice, usePageState } from './ui'

const statuses = { all: '全部', active: '进行中', paused: '已暂停', completed: '已完成', abandoned: '已放下' }
type PursuitFilter = keyof typeof statuses
type PursuitListResult = {
  status: PursuitFilter
  items: ApiSchemas['LifePursuitPublicView'][] | null
  failed: boolean
}

const states = {
  planned: '准备中',
  running: '正在继续',
  completed: '已完成',
  failed: '暂时没能继续',
  cancelled: '已取消',
  unknown: '还在等结果',
  mediaPaused: '制作已暂停',
  mediaQueued: '等待继续制作',
  reviewPending: '等待检查',
  permissionWait: '等待确认授权或身份'
}

type TimelinePage = ApiSchemas['LifeTrajectoryListResponse']

export function LifeSummary({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const [search, setSearch] = usePageState('life.search', emptyArchiveSearch())
  const t = archiveSearchCopy()
  const [life, setLife] = useState<ApiSchemas['LifeStatePublicView'] | null>(null)
  const [lifeFailed, setLifeFailed] = useState(false)
  const [list, setList] = useState<PursuitListResult | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [status, setStatus] = usePageState<PursuitFilter>('life.status', 'active')
  const [detail, setDetail] = useState<ApiSchemas['LifePursuitPublicDetail'] | null>(null)
  const [detailFailed, setDetailFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const [topicId, setTopicId] = usePageState<string | null>('life.topic', null)
  const [timeline, setTimeline] = useState<{ scope: string; page: TimelinePage } | null>(null)
  const [timelineBusy, setTimelineBusy] = useState(false)
  const [timelineError, setTimelineError] = useState(false)
  const request = useRef(0)
  const scope = JSON.stringify([selected, topicId])
  const currentTimeline = timeline?.scope === scope ? timeline.page : null
  const latest = life?.latest_episode
  const home = !topicId && !selected && !search.applied
  const entries = currentTimeline?.items.filter(entry => !home || entry.id !== latest?.id) ?? []
  const currentList = list?.status === status ? list : null
  const pursuits = currentList?.items ?? null

  const selectPursuit = (id: string | null): void => {
    if (id !== selected) {
      setSelected(id)
      setTopicId(null)
      setDetail(null)
      setDetailFailed(false)
    }
  }

  const selectTopic = (id: string | null): void => {
    setSelected(null)
    setDetail(null)
    setDetailFailed(false)
    setTopicId(id)
  }

  const loadTimeline = useCallback(
    async (cursor?: string): Promise<void> => {
      const generation = ++request.current
      setTimelineBusy(true)
      setTimelineError(false)

      try {
        const page = await api.invoke('companion_life.get_life_trajectory', {
          query: { cursor, pursuit_id: selected ?? undefined, topic_id: topicId ?? undefined }
        })
        if (request.current === generation) {
          setTimeline(previous => {
            if (!previous || previous.scope !== scope) {
              return { scope, page }
            }
            const previousIds = new Set(previous.page.items.map(entry => entry.id))
            const overlaps = page.items.some(entry => previousIds.has(entry.id))
            const merge = cursor !== undefined || (page.has_more && overlaps)

            if (!merge) {
              return { scope, page }
            }
            return {
              scope,
              page: {
                ...(cursor ? page : previous.page),
                items: [...new Map([...previous.page.items, ...page.items].map(entry => [entry.id, entry])).values()]
              }
            }
          })
        }
      } catch {
        if (request.current === generation) {
          setTimelineError(true)
        }
      } finally {
        if (request.current === generation) {
          setTimelineBusy(false)
        }
      }
    },
    [api, scope, selected, topicId]
  )

  const invalidateTimeline = useCallback(() => {
    request.current++
  }, [])

  useEffect(() => {
    if (!search.applied) {
      void loadTimeline()
    }

    return invalidateTimeline
  }, [invalidateTimeline, loadTimeline, refresh, retry, search.applied])

  const searchHistory = useCallback(
    (filters: ArchiveSearchFilters) =>
      api.invoke('companion_life.search_life_trajectory', {
        query: { ...archiveSearchQuery(filters), pursuit_id: selected ?? undefined, topic_id: topicId ?? undefined }
      }),
    [api, selected, topicId]
  )
  useEffect(() => {
    let current = true
    void api
      .invoke('companion_life.get_life', {})
      .then(state => {
        if (current) {
          setLife(state)
          setLifeFailed(false)
        }
      })
      .catch(() => {
        if (current) {
          setLifeFailed(true)
        }
      })
    return () => {
      current = false
    }
  }, [api, refresh, retry])
  useEffect(() => {
    let current = true
    void api
      .invoke('companion_life.get_life_pursuits', {
        query: { status: status === 'all' ? undefined : status, limit: 10 }
      })
      .then(response => {
        if (current) {
          setList({ status, items: response.items, failed: false })
        }
      })
      .catch(() => {
        if (current) {
          setList(previous => ({ status, items: previous?.status === status ? previous.items : null, failed: true }))
        }
      })
    return () => {
      current = false
    }
  }, [api, refresh, retry, status])
  useEffect(() => {
    let current = true
    if (selected) {
      void api
        .invoke('companion_life.get_life_pursuit', { path: { pursuit_id: selected } })
        .then(value => {
          if (current) {
            setDetail(value)
            setDetailFailed(false)
          }
        })
        .catch(() => {
          if (current) {
            setDetailFailed(true)
          }
        })
    }
    return () => {
      current = false
    }
  }, [api, selected, refresh, retry])
  return (
    <section aria-label="伙伴近况" className="card companion-life-card">
      {home &&
        (latest ? (
          <LifeEpisodeCard
            api={api}
            entry={latest}
            featured
            onPursuit={selectPursuit}
            onTopic={selectTopic}
            topic={topicId}
          />
        ) : (
          <p className="muted" role="status">
            {!life && !lifeFailed ? '正在了解近况…' : '暂时还没有可分享的近况'}
          </p>
        ))}
      {(lifeFailed || currentList?.failed || detailFailed) && (
        <Notice>
          暂时无法更新生活内容。
          <button onClick={() => setRetry(value => value + 1)} type="button">
            重试
          </button>
        </Notice>
      )}
      {selected && (
        <section>
          <button className="subtle" onClick={() => selectPursuit(null)} type="button">
            返回生活
          </button>
          {detail?.id === selected ? (
            <article>
              <small>{statuses[detail.status]}</small>
              <h2>{detail.title || '这件事'}</h2>
              {detail.narrative && <p>{detail.narrative}</p>}
              {detail.steps.length > 0 && (
                <details>
                  <summary>实际行动</summary>
                  {detail.steps.map(step => (
                    <section key={step.id}>
                      <strong>{states[lifeStepStatus(step)]}</strong>
                      {step.narrative && step.disposition !== 'waiting' && <p>{step.narrative}</p>}
                      {step.reading_result && <LifeReading reading={step.reading_result} />}
                      <LifeResultPreviews api={api} reference={step.result_ref} />
                    </section>
                  ))}
                </details>
              )}
            </article>
          ) : (
            !detailFailed && <p role="status">正在加载事项…</p>
          )}
        </section>
      )}
      <section>
        <h2>生活轨迹</h2>
        {topicId && (
          <div className="row-actions">
            <small>当前主题</small>
            <button className="subtle" onClick={() => selectTopic(null)} type="button">
              查看全部经历
            </button>
          </div>
        )}
        <ArchiveSearchPanel
          collapseLabel="搜索经历"
          count={result => result.items.length}
          label={t.life}
          load={searchHistory}
          onChange={setSearch}
          refresh={refresh}
          scope={scope}
          value={search}
        >
          {result => (
            <LifeTimeline
              api={api}
              entries={result.items}
              onPursuit={selectPursuit}
              onTopic={selectTopic}
              topic={topicId}
            />
          )}
        </ArchiveSearchPanel>
        {!search.applied && (
          <>
            {timelineError && (
              <Notice>
                暂时无法更新生活轨迹。
                <button disabled={timelineBusy} onClick={() => void loadTimeline()} type="button">
                  重试
                </button>
              </Notice>
            )}
            {entries.length > 0 ? (
              <LifeTimeline
                api={api}
                entries={entries}
                onPursuit={selectPursuit}
                onTopic={selectTopic}
                topic={topicId}
              />
            ) : (
              !timelineError && (
                <p className="muted" role="status">
                  {!currentTimeline ? '正在加载经历…' : '暂时没有更早的经历。'}
                </p>
              )
            )}
            {currentTimeline?.next_cursor && (
              <button
                className="subtle"
                disabled={timelineBusy}
                onClick={() => void loadTimeline(currentTimeline.next_cursor ?? undefined)}
                type="button"
              >
                更早的经历
              </button>
            )}
          </>
        )}
      </section>
      {!selected && (
        <details>
          <summary>在意的事</summary>
          <div aria-label="在意的事筛选" className="section-tabs life-filter-tabs">
            {(['active', 'paused', 'completed', 'abandoned', 'all'] as const).map(value => (
              <button
                aria-pressed={status === value}
                className={status === value ? 'selected' : ''}
                key={value}
                onClick={() => setStatus(value)}
                type="button"
              >
                {statuses[value]}
              </button>
            ))}
          </div>
          {pursuits && pursuits.length > 0 && (
            <ul>
              {pursuits.map(item => (
                <li key={item.id}>
                  <button onClick={() => selectPursuit(item.id)} type="button">
                    {statuses[item.status]} · {item.title || '这件事'}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {pursuits?.length === 0 && <p className="muted">这里还没有记录的事项。</p>}
          {!pursuits && !currentList?.failed && (
            <p className="muted" role="status">
              正在加载事项…
            </p>
          )}
        </details>
      )}
    </section>
  )
}
