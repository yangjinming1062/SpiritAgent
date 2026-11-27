import './life-reader.css'

import type { Dispatch, ReactNode, SetStateAction } from 'react'
import { useLayoutEffect, useRef } from 'react'

import type { ApiSchemas } from './api.generated'
import { archiveSearchCopy, emptyArchiveSearch } from './archive-search'
import { ArchiveSearchFeedback, ArchiveSearchForm } from './archive-search-form'
import { lifeCopy } from './life-copy'
import { LifeEpisodes, LifePursuitDetail, type LifeResultsView } from './life-episodes'
import type { LifeMediaTile } from './life-media'
import {
  type LifeDestination,
  type LifeFrame,
  lifePlaceKey,
  type LifeReaderState,
  type PursuitFilter,
  pursuitFilters,
  visitLife
} from './life-reader-state'
import { type ReadLife, useLifeReaderData } from './use-life-reader-data'

interface StatusSelectProps {
  value: PursuitFilter
  options: { value: PursuitFilter; label: string }[]
  ariaLabel: string
  onChange: (status: PursuitFilter) => void
}
type Copy = typeof lifeCopy.zh

function PursuitRow({ item, t, open }: { item: ApiSchemas['LifePursuitPublicView']; t: Copy; open: () => void }) {
  return (
    <button className="life-pursuit-row" data-life-open={'row-' + item.id} onClick={open} type="button">
      <span>
        <strong>{item.title || t.pursuitFallback}</strong>
        {item.narrative && <span className="life-excerpt">{item.narrative}</span>}
      </span>
      <span className="life-status">{t.statuses[item.status]}</span>
    </button>
  )
}

function Retry({ message, label, onRetry }: { message: string; label: string; onRetry: () => void }) {
  return (
    <div className="life-error" role="alert">
      <span>{message}</span>
      <button className="life-link" onClick={onRetry} type="button">
        {label}
      </button>
    </div>
  )
}

function readingKey(frames: LifeFrame[]): string {
  const current = frames[frames.length - 1]
  return frames.map(lifePlaceKey).join(':') + (current.kind === 'pursuits' ? ':' + current.status : '')
}

function heading(
  frame: LifeFrame,
  context: LifeFrame,
  entries: ApiSchemas['LifeTrajectoryEntry'][],
  onlyPursuits: boolean,
  t: Copy
): string | null {
  switch (frame.kind) {
    case 'home':
      return onlyPursuits ? t.pursuitsTitle : entries.length ? t.latest : null
    case 'pursuits':
      return t.pursuitsTitle
    case 'topic':
      return entries.find(entry => entry.topic_title)?.topic_title || t.topicScope
    case 'pursuit':
      return null
    case 'search':
      return context.kind === 'topic' ? t.searchTopic : context.kind === 'pursuit' ? t.searchPursuit : t.searchLabel
  }
}

export function LifeReader({
  locale,
  read,
  revision,
  visible = true,
  state,
  setState,
  restoreRevision = 0,
  results,
  mediaTile,
  statusSelect,
  getScroller
}: {
  locale: string
  read: ReadLife
  revision: string | number
  visible?: boolean
  state: LifeReaderState
  setState: Dispatch<SetStateAction<LifeReaderState>>
  restoreRevision?: number
  results: LifeResultsView
  mediaTile: LifeMediaTile
  statusSelect: (props: StatusSelectProps) => ReactNode
  getScroller: (root: HTMLElement) => HTMLElement | null
}) {
  const { frames } = state
  const frame = frames[frames.length - 1]
  const context = frame.kind === 'search' ? frames[frames.length - 2] : frame
  const placeKey = readingKey(frames)
  const { events, pursuits, detail, search } = useLifeReaderData(read, frame, context, visible, revision)
  const root = useRef<HTMLElement>(null)
  const pendingScroll = useRef<number | null>(frame.scrollTop)
  const focusTarget = useRef<string | null>(null)
  const triggers = useRef(new Map<string, string>())
  const position = useRef(frame.scrollTop)
  position.current = frame.scrollTop
  const t = locale === 'en' ? lifeCopy.en : lifeCopy.zh
  const archive = archiveSearchCopy(locale)
  const applied = frame.kind === 'search' && frame.search.applied !== null
  const entries = (applied ? search.data : events.data)?.items ?? []
  const items = pursuits.data?.items ?? []
  const home = frame.kind === 'home'
  const onlyPursuits = home && (events.data !== null || events.failed) && entries.length === 0 && items.length > 0
  const emptyHome =
    home &&
    events.data !== null &&
    pursuits.data !== null &&
    !events.busy &&
    !pursuits.busy &&
    !events.failed &&
    !pursuits.failed &&
    entries.length === 0 &&
    items.length === 0
  const resources = [events, pursuits, detail, search]
  const waiting =
    resources.some(query => query.busy && !query.data) ||
    (home && entries.length === 0 && items.length === 0 && (events.busy || pursuits.busy))
  const failed = resources.some(query => query.failed)
  const ready = resources.every(query => query.settled)

  useLayoutEffect(() => {
    const history = events.data
    const last = history?.items.at(-1)
    if (
      !visible ||
      !history ||
      !last ||
      events.busy ||
      events.failed ||
      context.page === 0 ||
      history.page !== context.page
    ) {
      return
    }
    const boundary = { page: history.page, id: last.id, occurred_at: last.occurred_at }
    const saved = context.boundary
    if (saved?.page === boundary.page && saved.id === boundary.id && saved.occurred_at === boundary.occurred_at) {
      return
    }
    const index = frames.indexOf(context)
    setState(previous => {
      if (previous.frames[index] !== context) {
        return previous
      }
      return { ...previous, frames: previous.frames.map((item, i) => (i === index ? { ...item, boundary } : item)) }
    })
  }, [visible, context, frames, events.data, events.busy, events.failed, setState])

  useLayoutEffect(() => {
    pendingScroll.current = position.current
    if (visible && root.current) {
      getScroller(root.current)?.scrollTo({ top: 0 })
    }
  }, [placeKey, restoreRevision, visible, getScroller])

  useLayoutEffect(() => {
    const scroller = root.current && getScroller(root.current)
    if (!scroller || !visible) {
      return
    }
    if (pendingScroll.current !== null) {
      if (!ready || (failed && pendingScroll.current > scroller.scrollHeight - scroller.clientHeight)) {
        return
      }
      scroller.scrollTop = pendingScroll.current
      pendingScroll.current = null
    }
    if (focusTarget.current && root.current) {
      const target = root.current.querySelector<HTMLElement>(focusTarget.current) ?? root.current
      target.focus({ preventScroll: true })
      focusTarget.current = null
    }
    const save = () => {
      const top = scroller.scrollTop
      setState(previous => {
        const current = previous.frames[previous.frames.length - 1]
        if (readingKey(previous.frames) !== placeKey || current.scrollTop === top) {
          return previous
        }
        return { ...previous, frames: [...previous.frames.slice(0, -1), { ...current, scrollTop: top }] }
      })
    }
    scroller.addEventListener('scroll', save, { passive: true })
    return () => scroller.removeEventListener('scroll', save)
  }, [placeKey, restoreRevision, visible, ready, failed, getScroller, setState])

  const update = (next: LifeFrame) => setState({ ...state, frames: [...frames.slice(0, -1), next] })
  const navigate = (place: LifeDestination) => {
    const trigger = root.current?.ownerDocument.activeElement
    if (trigger instanceof HTMLElement && trigger.dataset.lifeOpen) {
      triggers.current.set(placeKey, trigger.dataset.lifeOpen)
    }
    focusTarget.current = place.kind === 'search' ? 'input[type="search"]' : '.life-back'

    const top = root.current && getScroller(root.current)?.scrollTop
    const current = { ...frame, scrollTop: pendingScroll.current ?? top ?? frame.scrollTop }
    setState(visitLife({ ...state, frames: [...frames.slice(0, -1), current] }, place))
  }
  const back = () => {
    const previous = frames.slice(0, -1)
    const parentKey = readingKey(previous)
    const trigger = triggers.current.get(parentKey)
    triggers.current.delete(parentKey)
    focusTarget.current = trigger ? '[data-life-open="' + CSS.escape(trigger) + '"]' : ':scope'
    setState({ frames: previous })
  }
  const openList = () => navigate({ kind: 'pursuits', status: 'all' })
  const selectStatus = (status: PursuitFilter) => update({ kind: 'pursuits', status, page: 0, scrollTop: 0 })
  const openSearch = () => navigate({ kind: 'search', search: emptyArchiveSearch() })
  const firstPursuit = items[0]
  const row = (item: typeof firstPursuit) => (
    <PursuitRow item={item} open={() => navigate({ kind: 'pursuit', id: item.id })} t={t} />
  )

  const title = heading(frame, context, entries, onlyPursuits, t)
  const canSearch = frame.kind !== 'pursuits' && frame.kind !== 'search' && entries.length > 0

  return (
    <section aria-label={t.now} className="life-reader" ref={root} tabIndex={-1}>
      {frame.kind !== 'home' && (
        <button className="life-back" onClick={back} type="button">
          {t.back}
        </button>
      )}
      {frame.kind === 'pursuit' && detail.data && (
        <LifePursuitDetail detail={detail.data} locale={locale} results={results} t={t} />
      )}
      {detail.failed && <Retry label={t.retry} message={t.detailFailed} onRetry={detail.retry} />}
      {(title || canSearch) && (
        <header className="life-reader-header">
          {title ? <h1>{title}</h1> : <h2>{t.related}</h2>}
          <div className="life-actions">
            {canSearch && (
              <button className="life-tool" data-life-open="search" onClick={openSearch} type="button">
                {t.searchLabel}
              </button>
            )}
            {home && entries.length > 0 && firstPursuit && (
              <button className="life-tool" data-life-open="list" onClick={openList} type="button">
                {t.pursuitsTitle}
              </button>
            )}
            {onlyPursuits && (items.length > 1 || pursuits.data?.next_cursor) && (
              <button className="life-tool" data-life-open="list" onClick={openList} type="button">
                {t.allPursuits}
              </button>
            )}
            {frame.kind === 'pursuits' &&
              statusSelect({
                value: frame.status,
                options: pursuitFilters.map(value => ({ value, label: t.statuses[value] })),
                ariaLabel: t.statusFilter,
                onChange: selectStatus
              })}
          </div>
        </header>
      )}
      {frame.kind === 'search' && (
        <ArchiveSearchForm
          busy={search.busy}
          label={title ?? archive.life}
          locale={locale}
          onChange={next => update({ ...frame, search: next })}
          onClear={back}
          queryLabel={archive.query}
          value={frame.search}
        />
      )}
      {search.failed && <Retry label={archive.retry} message={archive.failed} onRetry={search.retry} />}
      {applied && search.data && (
        <ArchiveSearchFeedback
          className="life-feedback"
          count={search.data.items.length}
          locale={locale}
          status={search.data.search}
        />
      )}
      {events.failed && <Retry label={t.retry} message={t.eventsFailed} onRetry={events.retry} />}
      {entries.length > 0 && (
        <LifeEpisodes
          active={visible}
          context={context}
          entries={entries}
          locale={locale}
          mediaTile={mediaTile}
          navigate={navigate}
          results={results}
          t={t}
        />
      )}
      {frame.kind === 'topic' && events.data && !events.failed && entries.length === 0 && (
        <p className="life-empty">{t.emptyTopic}</p>
      )}
      {frame.kind !== 'search' &&
        events.data &&
        (events.data.next_cursor !== null || events.data.buffered.length > 0) && (
          <button
            className="life-more"
            disabled={events.busy}
            onClick={() => update({ ...frame, page: frame.page + 1 })}
            type="button"
          >
            {t.moreEvents}
          </button>
        )}
      {pursuits.failed && <Retry label={t.retry} message={t.pursuitsFailed} onRetry={pursuits.retry} />}
      {onlyPursuits && row(firstPursuit)}
      {frame.kind === 'pursuits' && (
        <>
          {items.length > 0 && (
            <ul className="life-pursuit-list">
              {items.map(item => (
                <li key={item.id}>{row(item)}</li>
              ))}
            </ul>
          )}
          {pursuits.data && !pursuits.failed && items.length === 0 && <p className="life-empty">{t.emptyPursuits}</p>}
          {pursuits.data?.next_cursor && (
            <button
              className="life-more"
              disabled={pursuits.busy}
              onClick={() => update({ ...frame, page: frame.page + 1 })}
              type="button"
            >
              {t.morePursuits}
            </button>
          )}
        </>
      )}
      {emptyHome && (
        <p className="life-empty" role="status">
          {t.empty}
        </p>
      )}
      {waiting && (
        <p className="life-loading" role="status">
          {applied ? archive.loading : t.loading}
        </p>
      )}
    </section>
  )
}
