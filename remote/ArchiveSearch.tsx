import type { ReactElement, ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'

import type { ApiSchemas } from '../shared/api.generated'
import {
  archiveSearchCopy,
  type ArchiveSearchFilters,
  type ArchiveSearchState,
  emptyArchiveSearch
} from '../shared/archive-search'

import { Notice, useTask } from './ui'

export function ArchiveSearchPanel<T extends { search: ApiSchemas['SearchStatus'] }>({
  value,
  onChange,
  label,
  load,
  refresh,
  count,
  children,
  scope = ''
}: {
  value: ArchiveSearchState
  onChange: (value: ArchiveSearchState) => void
  label: string
  load: (filters: ArchiveSearchFilters) => Promise<T>
  refresh: number
  count: (result: T) => number
  children: (result: T) => ReactNode
  scope?: string
}): ReactElement {
  const t = archiveSearchCopy()
  const { runLatest, busy, error } = useTask()
  const [result, setResult] = useState<T | null>(null)
  const query = useRef('')
  const applied = value.applied
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    let current = true
    const key = JSON.stringify([applied, scope])
    if (query.current !== key) {
      query.current = key
      setResult(null)
    }
    if (applied) {
      void runLatest(async () => {
        try {
          const next = await load(applied)
          if (current) {
            setResult(next)
          }
        } catch (failure) {
          if (current) {
            throw failure
          }
        }
      })
    }
    return () => {
      current = false
    }
  }, [applied, load, refresh, retry, runLatest, scope])

  return (
    <div className="archive-search">
      <form
        aria-label={label}
        className="archive-search-form"
        onSubmit={event => {
          event.preventDefault()
          onChange({ ...value, applied: { ...value.draft } })
        }}
      >
        <label className="archive-search-query">
          {label}
          <input
            maxLength={1000}
            onChange={event => onChange({ ...value, draft: { ...value.draft, query: event.target.value } })}
            placeholder={t.query}
            type="search"
            value={value.draft.query}
          />
        </label>
        <label>
          {t.from}
          <input
            max={value.draft.dateTo || undefined}
            onChange={event => onChange({ ...value, draft: { ...value.draft, dateFrom: event.target.value } })}
            type="date"
            value={value.draft.dateFrom}
          />
        </label>
        <label>
          {t.to}
          <input
            min={value.draft.dateFrom || undefined}
            onChange={event => onChange({ ...value, draft: { ...value.draft, dateTo: event.target.value } })}
            type="date"
            value={value.draft.dateTo}
          />
        </label>
        <div className="row-actions">
          <button aria-busy={busy} disabled={busy} type="submit">
            {busy ? t.loading : t.search}
          </button>
          <button className="subtle" onClick={() => onChange(emptyArchiveSearch())} type="button">
            {t.clear}
          </button>
        </div>
      </form>
      {applied && (
        <>
          {error && (
            <Notice>
              {t.failed}
              <button onClick={() => setRetry(value => value + 1)} type="button">
                {t.retry}
              </button>
            </Notice>
          )}
          {busy && !result && <p role="status">{t.loading}</p>}
          {result && (
            <>
              <div aria-live="polite" className="muted">
                {result.search.indexing && <p>{t.indexing}</p>}
                {result.search.mode === 'keyword' && <p>{t.keyword}</p>}
                {count(result) === 0 && <p>{t.empty}</p>}
                {count(result) >= 20 && <p>{t.limit}</p>}
              </div>
              {children(result)}
            </>
          )}
        </>
      )}
    </div>
  )
}
