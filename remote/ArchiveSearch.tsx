import type { ReactElement, ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'

import type { ApiSchemas } from '../shared/api.generated'
import { archiveSearchCopy, type ArchiveSearchFilters, type ArchiveSearchState } from '../shared/archive-search'
import { ArchiveSearchFeedback, ArchiveSearchForm } from '../shared/archive-search-form'

import { useTask } from './tasks'
import { Notice } from './ui'

export function ArchiveSearchPanel<T extends { search: ApiSchemas['SearchStatus'] }>({
  value,
  onChange,
  label,
  load,
  refresh,
  count,
  children
}: {
  value: ArchiveSearchState
  onChange: (value: ArchiveSearchState) => void
  label: string
  load: (filters: ArchiveSearchFilters) => Promise<T>
  refresh: number
  count: (result: T) => number
  children: (result: T) => ReactNode
}): ReactElement {
  const t = archiveSearchCopy()
  const { runLatest, busy, error } = useTask()
  const [result, setResult] = useState<T | null>(null)
  const query = useRef('')
  const applied = value.applied
  const [retry, setRetry] = useState(0)

  useEffect(() => {
    let current = true
    const key = JSON.stringify(applied)
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
  }, [applied, load, refresh, retry, runLatest])

  return (
    <div className="archive-search">
      <ArchiveSearchForm busy={busy} label={label} onChange={onChange} value={value} />
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
              <ArchiveSearchFeedback className="muted" count={count(result)} status={result.search} />
              {children(result)}
            </>
          )}
        </>
      )}
    </div>
  )
}
