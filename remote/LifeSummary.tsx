import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { ApiSchemas } from '../shared/api.generated'
import {
  archiveSearchCopy,
  type ArchiveSearchFilters,
  archiveSearchQuery,
  emptyArchiveSearch
} from '../shared/archive-search'

import type { RemoteApi } from './api'
import { ArchiveSearchPanel } from './ArchiveSearch'
import { usePageState } from './ui'

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
  unknown: '还在等结果'
}

export function LifeSummary({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const [search, setSearch] = usePageState('life.search', emptyArchiveSearch())
  const pursuitsElement = useRef<HTMLDetailsElement>(null)
  const t = archiveSearchCopy()
  const [life, setLife] = useState<ApiSchemas['LifeStatePublicView'] | null>(null)
  const [lifeFailed, setLifeFailed] = useState(false)
  const [list, setList] = useState<PursuitListResult | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [status, setStatus] = usePageState<PursuitFilter>('life.status', 'active')
  const [detail, setDetail] = useState<ApiSchemas['LifePursuitPublicDetail'] | null>(null)
  const [detailFailed, setDetailFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const currentList = list?.status === status ? list : null
  const pursuits = currentList?.items ?? null

  const selectPursuit = (id: string | null): void => {
    if (id !== selected) {
      setSelected(id)
      setDetail(null)
      setDetailFailed(false)
    }
  }

  const searchHistory = useCallback(
    (filters: ArchiveSearchFilters) =>
      api.invoke('companion_life.search_life_events', {
        query: { ...archiveSearchQuery(filters), pursuit_id: selected ?? undefined }
      }),
    [api, selected]
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
      <p>{life?.narrative || '暂时还没有可分享的近况'}</p>
      {(lifeFailed || currentList?.failed || detailFailed) && (
        <div role="status">
          <p>暂时无法更新，请重试。</p>
          <button onClick={() => setRetry(value => value + 1)} type="button">
            重试
          </button>
        </div>
      )}
      <details ref={pursuitsElement}>
        <summary>在意的事</summary>
        <div aria-label="在意的事筛选" className="section-tabs life-filter-tabs">
          {(['active', 'paused', 'completed', 'abandoned', 'all'] as const).map(value => (
            <button
              aria-pressed={status === value}
              className={status === value ? 'selected' : ''}
              key={value}
              onClick={() => {
                if (status !== value) {
                  setStatus(value)
                  selectPursuit(null)
                }
              }}
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
                  {statuses[item.status]} · {item.narrative || '这件事'}
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
        {detail?.id === selected && (
          <article>
            <h3>{detail.narrative || '这件事'}</h3>
            <small>{new Date(detail.started_at).toLocaleString()}</small>
            {detail.status_changed_at && (
              <small>
                {statuses[detail.status]} · {new Date(detail.status_changed_at).toLocaleString()}
              </small>
            )}
            {detail.steps.map(step => (
              <section key={step.id}>
                <strong>{states[step.state]}</strong>
                {step.narrative && <p>{step.narrative}</p>}
                {step.reading_result && (
                  <details>
                    <summary>阅读笔记</summary>
                    <p style={{ whiteSpace: 'pre-wrap' }}>{step.reading_result.notes}</p>
                    <ul>
                      {step.reading_result.sources.map(source => (
                        <li key={source.url}>
                          <a href={source.url} rel="noreferrer" target="_blank">
                            {source.title || source.url}
                          </a>{' '}
                          · {source.content_kind === 'summary' ? '摘要' : source.excerpted ? '摘录' : '正文'} ·{' '}
                          {new Date(source.retrieved_at).toLocaleString()}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </section>
            ))}
          </article>
        )}
      </details>
      <ArchiveSearchPanel
        count={result => result.items.length}
        label={t.life}
        load={searchHistory}
        onChange={setSearch}
        refresh={refresh}
        scope={selected ?? ''}
        value={search}
      >
        {result => (
          <ol className="archive-life-results">
            {result.items.map(item => (
              <li key={item.id}>
                <time>{new Date(item.occurred_at).toLocaleString()}</time>
                <small>
                  {t.subjects[item.subject]} · {t.facts[item.fact_type]}
                </small>
                {item.narrative && <p>{item.narrative}</p>}
                {item.reading_result && (
                  <details>
                    <summary>{t.notes}</summary>
                    <p>{item.reading_result.notes}</p>
                    <ul>
                      {item.reading_result.sources.map(source => (
                        <li key={source.url}>
                          <a href={source.url} rel="noreferrer" target="_blank">
                            {source.title || source.url}
                          </a>
                          {' · '}
                          {source.content_kind === 'summary' ? '摘要' : source.excerpted ? '摘录' : '正文'}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {item.pursuit_id && (
                  <button
                    className="subtle"
                    onClick={() => {
                      selectPursuit(item.pursuit_id)
                      if (pursuitsElement.current) {
                        pursuitsElement.current.open = true
                        pursuitsElement.current.scrollIntoView({ block: 'nearest' })
                      }
                    }}
                    type="button"
                  >
                    {t.details}
                  </button>
                )}
              </li>
            ))}
          </ol>
        )}
      </ArchiveSearchPanel>
    </section>
  )
}
