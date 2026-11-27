import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  archiveSearchCopy,
  type ArchiveSearchFilters,
  archiveSearchQuery,
  emptyArchiveSearch
} from '../shared/archive-search'

import type { RemoteApi } from './api'
import { ArchiveSearchPanel } from './ArchiveSearch'
import { usePageScroll, usePageState } from './page-state'
import { useAlive, useTask } from './tasks'
import type { Diary } from './types'
import { Empty, Notice } from './ui'

// 没有游标与 has_more：整页返回表示可能还有更早的日记。
const DIARY_PAGE_SIZE = 100

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
  // 日记详情随组件生命周期，切 tab 即回列表；日期筛选保留在 usePageState。
  const [selected, setSelected] = useState<string | null>(null)
  const [to, setTo] = usePageState('diary.to', '')
  const [search, setSearch] = usePageState('diary.search', emptyArchiveSearch())
  const tSearch = archiveSearchCopy()
  const searchEntries = useCallback(
    (filters: ArchiveSearchFilters) =>
      api.invoke('companion_journal.search_diary_entries', { query: archiveSearchQuery(filters) }),
    [api]
  )
  const request = useRef(0)

  const load = useCallback(async () => {
    const revision = ++request.current
    const result = await api.invoke('companion_journal.get_diary', {
      query: { to: to || undefined, limit: DIARY_PAGE_SIZE }
    })

    if (alive() && revision === request.current) {
      setEntries(result.entries)
      setLoaded(true)
    }
  }, [api, alive, to])

  useEffect(() => {
    if (!search.applied) {
      void runLatest(load)
    }
  }, [refresh, load, runLatest, search.applied])
  const entry = entries.find(value => value.id === selected)
  const reading = useDiaryRead(api, search.applied ? null : (entry?.id ?? null), onRead)
  usePageScroll(`diary.${selected ?? 'list'}.${to}`, loaded && !search.applied)

  return (
    <>
      <ArchiveSearchPanel
        count={result => result.entries.length}
        label={tSearch.diary}
        load={searchEntries}
        onChange={setSearch}
        refresh={refresh}
        value={search}
      >
        {result => (
          <DiarySearchResults
            api={api}
            entries={result.entries}
            key={JSON.stringify(search.applied)}
            onRead={onRead}
            scrollKey={`diary.search.${JSON.stringify(search.applied)}`}
          />
        )}
      </ArchiveSearchPanel>
      {!search.applied && (
        <>
          {error ? <Notice>{error}</Notice> : null}
          {reading.error && (
            <Notice>
              {reading.error}
              <button onClick={reading.retry} type="button">
                重试同步已读
              </button>
            </Notice>
          )}
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
          {entries.length === DIARY_PAGE_SIZE ? (
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
      )}
    </>
  )
}

function useDiaryRead(api: RemoteApi, id: string | null, onRead: () => void) {
  const alive = useAlive()
  const { runLatest, error } = useTask()
  const callback = useRef(onRead)
  callback.current = onRead
  const [retryKey, setRetryKey] = useState(0)
  useEffect(() => {
    let current = true
    let done = false
    const read = (): void => {
      if (!id || done || document.visibilityState !== 'visible' || !document.hasFocus()) {
        return
      }
      void runLatest(async () => {
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
        if (!current || !alive() || document.visibilityState !== 'visible' || !document.hasFocus()) {
          return
        }

        await api.invoke('companion_journal.read_diary', { body: { diary_ids: [id] } })
        if (current && alive()) {
          done = true
          callback.current()
        }
      })
    }
    read()
    window.addEventListener('focus', read)
    document.addEventListener('visibilitychange', read)
    return () => {
      current = false
      window.removeEventListener('focus', read)
      document.removeEventListener('visibilitychange', read)
    }
  }, [api, id, alive, runLatest, retryKey])
  return { error, retry: () => setRetryKey(value => value + 1) }
}

function DiarySearchResults({
  api,
  entries,
  onRead,
  scrollKey
}: {
  api: RemoteApi
  entries: Diary[]
  onRead: () => void
  scrollKey: string
}): ReactElement {
  const [selected, setSelected] = useState<string | null>(null)
  const entry = entries.find(item => item.id === selected)
  usePageScroll(`${scrollKey}.${selected ?? 'list'}`, true)
  const reading = useDiaryRead(api, entry?.id ?? null, onRead)
  return (
    <>
      {reading.error && (
        <Notice>
          {reading.error}
          <button onClick={reading.retry} type="button">
            重试同步已读
          </button>
        </Notice>
      )}
      {entry ? (
        <article className="diary-entry">
          <button className="subtle" onClick={() => setSelected(null)} type="button">
            ‹ 返回搜索结果
          </button>
          <time>{entry.entry_date}</time>
          <h2>{entry.title}</h2>
          <div className="diary-body">{entry.body}</div>
        </article>
      ) : (
        <div className="diary-list">
          {entries.map(item => (
            <button className="diary-preview" key={item.id} onClick={() => setSelected(item.id)} type="button">
              <time>{item.entry_date}</time>
              <h3>{item.title}</h3>
              <p>
                {item.body.slice(0, 160)}
                {item.body.length > 160 ? '…' : ''}
              </p>
            </button>
          ))}
        </div>
      )}
    </>
  )
}
