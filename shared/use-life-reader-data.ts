import { useCallback, useEffect, useRef, useState } from 'react'

import type { ApiInput, ApiResponse } from './api'
import { archiveSearchQuery } from './archive-search'
import { type ReadPages, readPages } from './life-reader-pages'
import type { LifeFrame } from './life-reader-state'
import { compareLifeEpisodes } from './life-trajectory'

type LifeOperation =
  | 'companion_life.get_life_trajectory'
  | 'companion_life.get_life_pursuits'
  | 'companion_life.get_life_pursuit'
  | 'companion_life.search_life_trajectory'
export type ReadLife = <K extends LifeOperation>(operation: K, input: ApiInput<K>) => Promise<ApiResponse<K>>
type Revision = string | number

type LifeLoader<T> = (previous: T | null, reusePages: boolean, current: () => boolean) => Promise<T>

interface LifeRequest<T> {
  key: string
  revision: Revision
  load: LifeLoader<T>
}

function useLifeQuery<T>(key: string | null, revision: Revision, load: LifeLoader<T>) {
  const [result, setResult] = useState<(LifeRequest<T> & { data: T }) | null>(null)
  const latest = useRef(result)
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<LifeRequest<T> | null>(null)
  const [retry, setRetry] = useState(0)

  const requested = useRef<LifeRequest<T> | null>(null)
  const queued = useRef(new Map<string, (() => void) | null>())

  useEffect(() => {
    if (key === null) {
      return
    }
    const request = { key, revision, load }
    requested.current = request
    const current = () => requested.current?.key === key && requested.current.load === load
    setPending(true)
    setFailure(null)

    const run = async () => {
      queued.current.set(key, null)
      const saved = latest.current
      try {
        const reusePages = saved?.key === key && saved.revision === revision && saved.load !== load
        const data = await load(saved?.key === key ? saved.data : null, reusePages, current)
        if (current()) {
          latest.current = { ...request, data }
          setResult(latest.current)
        }
      } catch (error) {
        if (requested.current === request) {
          console.warn('生活内容读取失败', error instanceof Error ? error.message : error)
          setFailure(request)
        }
      } finally {
        if (requested.current === request) {
          setPending(false)
        }
        const next = queued.current.get(key)
        queued.current.delete(key)
        next?.()
      }
    }

    // 相同查询只留一次待执行刷新；切换关键词或范围时可以立即开始另一笔读取。
    const start = () => void run()
    const queue = queued.current
    if (queue.has(key)) {
      queue.set(key, start)
    } else {
      start()
    }
    return () => {
      requested.current = null
      if (queue.get(key) === start) {
        queue.set(key, null)
      }
    }
  }, [key, revision, load, retry])

  const data = result?.key === key ? result.data : null
  const matches = (request: LifeRequest<T> | null) =>
    request !== null && request.key === key && request.revision === revision && request.load === load
  const failed = matches(failure)
  const settled = key === null || matches(result) || failed
  return {
    data,
    failed,
    settled,
    busy: key !== null && (pending || !settled),
    retry: () => setRetry(value => value + 1)
  }
}

type Episode = ApiResponse<'companion_life.get_life_trajectory'>['items'][number]
type Pursuit = ApiResponse<'companion_life.get_life_pursuits'>['items'][number]

export function useLifeReaderData(
  read: ReadLife,
  frame: LifeFrame,
  context: LifeFrame,
  visible: boolean,
  revision: Revision
) {
  const pursuit = context.kind === 'pursuit' ? context.id : undefined
  const topic = context.kind === 'topic' ? context.id : undefined
  const applied = frame.kind === 'search' ? frame.search.applied : null
  const scope = [pursuit, topic].join('/')
  const eventPage = context.page
  const boundary = context.boundary
  const listPage = frame.kind === 'pursuits' ? frame.page : 0
  const status = frame.kind === 'pursuits' ? frame.status : 'all'
  const loadEvents = useCallback(
    (previous: ReadPages<Episode> | null, sameRevision: boolean, current: () => boolean) => {
      const last = previous?.items.at(-1)
      const atBoundary = boundary && last && compareLifeEpisodes(last, boundary) === 0
      if (sameRevision && previous?.page === eventPage && boundary?.page === eventPage && atBoundary) {
        return Promise.resolve(previous)
      }
      return readPages(
        eventPage,
        (cursor, limit) =>
          read('companion_life.get_life_trajectory', {
            query: { pursuit_id: pursuit, topic_id: topic, cursor, limit }
          }),
        sameRevision && (!boundary || (previous?.page === boundary.page && atBoundary)) ? previous : null,
        current,
        boundary && { page: boundary.page, compare: item => compareLifeEpisodes(item, boundary) }
      )
    },
    [read, eventPage, boundary, pursuit, topic]
  )
  const loadPursuits = useCallback(
    (previous: ReadPages<Pursuit> | null, sameRevision: boolean, current: () => boolean) =>
      readPages(
        listPage,
        (cursor, limit) =>
          read('companion_life.get_life_pursuits', {
            query: { status: status === 'all' ? undefined : status, cursor, limit }
          }),
        sameRevision ? previous : null,
        current
      ),
    [read, listPage, status]
  )
  const loadDetail = useCallback(
    () => read('companion_life.get_life_pursuit', { path: { pursuit_id: pursuit! } }),
    [read, pursuit]
  )
  const loadSearch = useCallback(
    () =>
      read('companion_life.search_life_trajectory', {
        query: { ...archiveSearchQuery(applied!), pursuit_id: pursuit, topic_id: topic }
      }),
    [read, applied, pursuit, topic]
  )

  const events = useLifeQuery(visible && context.kind !== 'pursuits' && !applied ? scope : null, revision, loadEvents)
  const pursuits = useLifeQuery(
    visible && (frame.kind === 'home' || frame.kind === 'pursuits') ? status : null,
    revision,
    loadPursuits
  )
  const detail = useLifeQuery(visible && frame.kind === 'pursuit' ? frame.id : null, revision, loadDetail)
  const search = useLifeQuery(visible && applied ? JSON.stringify([scope, applied]) : null, revision, loadSearch)
  return { events, pursuits, detail, search }
}
