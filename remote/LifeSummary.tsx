import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'

import type { ApiSchemas } from '../shared/api.generated'

import type { RemoteApi } from './api'

const statuses = { active: '进行中', paused: '已暂停', completed: '已完成', abandoned: '已放下' }
const states = {
  planned: '等待执行',
  running: '执行中',
  completed: '已完成',
  failed: '执行失败',
  cancelled: '已取消',
  unknown: '结果待核对'
}
const facts = { intent: '打算', virtual_activity: '生活情景', execution: '实际执行', result: '已确认成果' }

export function LifeSummary({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const [life, setLife] = useState<ApiSchemas['LifeStateView'] | null>(null)
  const [pursuits, setPursuits] = useState<ApiSchemas['LifePursuitView'][]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<ApiSchemas['LifePursuitDetail'] | null>(null)
  const [failed, setFailed] = useState(false)
  const [detailFailed, setDetailFailed] = useState(false)
  const [retry, setRetry] = useState(0)
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
      <p>{life?.current_activity || '暂时还没有近况'}</p>
      {life && (
        <small>
          {facts[life.activity_kind]}
          {life.activity_started_at && <> · {new Date(life.activity_started_at).toLocaleString()}</>}
        </small>
      )}
      {life?.mood && <small>{life.mood}</small>}
      {(failed || detailFailed) && (
        <div role="status">
          <p>暂时无法更新，已有内容仍可查看。</p>
          <button onClick={() => setRetry(value => value + 1)} type="button">
            重试
          </button>
        </div>
      )}
      {pursuits.length > 0 && (
        <details>
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
                  {statuses[item.status]} · {item.title}
                </button>
              </li>
            ))}
          </ul>
          {detail?.id === selected && (
            <article>
              <h3>{detail.title}</h3>
              {detail.status_changed_at && (
                <small>
                  {statuses[detail.status]} · {new Date(detail.status_changed_at).toLocaleString()}
                </small>
              )}
              <p>{detail.reason}</p>
              <p>{detail.desired_result}</p>
              <p>{detail.progress || '暂时没有已确认的进展'}</p>
              {detail.status_reason && <p>{detail.status_reason}</p>}
              {detail.status === 'active' && detail.next_step && <p>接下来打算：{detail.next_step}</p>}
              {detail.steps.map(step => (
                <section key={step.id}>
                  <strong>
                    {states[step.state]} · {step.description}
                  </strong>
                  {(step.wait_reason || step.error) && <p>{step.wait_reason || step.error}</p>}
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
    </section>
  )
}
