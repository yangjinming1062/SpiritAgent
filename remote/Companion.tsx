import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { requestApi } from '../shared/api'

import type { RemoteApi } from './api'
import { Assets } from './Assets'
import { MemoryView } from './Memory'
import { Profile } from './Profile'
import type { Diary } from './types'
import { Empty, Notice, PageHeader, useAlive, usePageScroll, usePageState, useTask } from './ui'

type Section = 'profile' | 'memory' | 'scene' | 'outfit'

export function Companion({ api, refresh }: { api: RemoteApi; refresh: number }): ReactElement {
  const [savedSection, setSection] = usePageState<Section>('companion.section', 'profile')
  const items = [
    { id: 'profile', title: '资料' },
    { id: 'outfit', title: '衣柜' },
    { id: 'scene', title: '窗口背景' },
    { id: 'memory', title: '记忆' }
  ] as const
  const section = items.some(item => item.id === savedSection) ? savedSection : 'profile'

  return (
    <div className="page companion-page">
      <nav aria-label="伙伴分类" className="companion-nav">
        {items.map(item => (
          <button
            aria-current={section === item.id ? 'page' : undefined}
            key={item.id}
            onClick={() => setSection(item.id)}
            type="button"
          >
            {item.title}
          </button>
        ))}
      </nav>
      {section === 'profile' ? (
        <>
          <PageHeader subtitle="名字、性格和你们的相处方式" title="伙伴资料" />
          <Profile api={api} refresh={refresh} />
        </>
      ) : section === 'memory' ? (
        <>
          <PageHeader subtitle="查看、修改或忘记聊天中记住的事" title="记忆" />
          <MemoryView api={api} refresh={refresh} />
        </>
      ) : (
        <Assets api={api} key={section} kind={section} refresh={refresh} />
      )}
    </div>
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
    const result = await requestApi(api, 'companion_journal.get_diary', {
      query: { from: from || undefined, to: to || undefined, limit: 100 }
    })

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
        await requestApi(api, 'companion_journal.read_diary', { body: { diary_ids: result.unread_diary_ids } })
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
