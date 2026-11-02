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

const statuses = { active: '进行中', paused: '已暂停', completed: '已完成', abandoned: '已放下' }
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
  const [pursuits, setPursuits] = useState<ApiSchemas['LifePursuitPublicView'][]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<ApiSchemas['LifePursuitPublicDetail'] | null>(null)
  const [failed, setFailed] = useState(false)
  const [detailFailed, setDetailFailed] = useState(false)
  const [retry, setRetry] = useState(0)
  const searchHistory = useCallback(
    (filters: ArchiveSearchFilters) =>
      api.invoke('companion_life.search_life_events', {
        query: { ...archiveSearchQuery(filters), pursuit_id: selected ?? undefined }
      }),
    [api, selected]
  )
  useEffect(() => {
    let current = true
    void Promise.all([
      api.invoke('companion_life.get_life', {}),
      api.invoke('companion_life.get_life_pursuits', { query: { limit: 10 } })
    ])
      .then(([state, list]) => {
        if (current) {
          setLife(state)
          setPursuits(list.items)
          setFailed(false)
        }
      })
      .catch(() => {
        if (current) {
          setFailed(true)
        }
      })
    return () => {
      current = false
    }
  }, [api, refresh, retry])
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
      <strong>伙伴近况</strong>
      <p>{life?.narrative || '暂时还没有可分享的近况'}</p>
      {(failed || detailFailed) && (
        <div role="status">
          <p>暂时无法更新，已有内容仍可查看。</p>
          <button onClick={() => setRetry(value => value + 1)} type="button">
            重试
          </button>
        </div>
      )}
      {pursuits.length > 0 && (
        <details ref={pursuitsElement}>
          <summary>在意的事</summary>
          <ul>
            {pursuits.map(item => (
              <li key={item.id}>
                <button
                  onClick={() => {
                    setSelected(item.id)
                    setDetailFailed(false)
                  }}
                  type="button"
                >
                  {statuses[item.status]} · {item.narrative || '这件事'}
                </button>
              </li>
            ))}
          </ul>
          {detail?.id === selected && (
            <article>
              <h3>{detail.narrative || '这件事'}</h3>
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
      )}
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
                      setSelected(item.pursuit_id)
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
