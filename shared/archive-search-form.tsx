import type React from 'react'

import type { ApiSchemas } from './api.generated'
import { archiveSearchCopy, type ArchiveSearchState, emptyArchiveSearch } from './archive-search'

export function ArchiveSearchForm({
  value,
  onChange,
  label,
  queryLabel = label,
  locale = 'zh',
  classes = {},
  onClear,
  busy = false
}: {
  value: ArchiveSearchState
  onChange: (value: ArchiveSearchState) => void
  label: string
  queryLabel?: string
  locale?: string
  classes?: { form?: string; query?: string; input?: string; actions?: string; button?: string }
  onClear?: () => void
  busy?: boolean
}): React.JSX.Element {
  const t = archiveSearchCopy(locale)

  return (
    <form
      aria-label={label}
      className={classes.form ?? 'archive-search-form'}
      onSubmit={event => {
        event.preventDefault()
        onChange({ ...value, applied: { ...value.draft } })
      }}
    >
      <label className={classes.query ?? 'archive-search-query'}>
        <span>{queryLabel}</span>
        <input
          className={classes.input}
          maxLength={1000}
          onChange={event => onChange({ ...value, draft: { ...value.draft, query: event.target.value } })}
          placeholder={queryLabel === t.query ? undefined : t.query}
          type="search"
          value={value.draft.query}
        />
      </label>
      <label>
        <span>{t.from}</span>
        <input
          className={classes.input}
          max={value.draft.dateTo || undefined}
          onChange={event => onChange({ ...value, draft: { ...value.draft, dateFrom: event.target.value } })}
          type="date"
          value={value.draft.dateFrom}
        />
      </label>
      <label>
        <span>{t.to}</span>
        <input
          className={classes.input}
          min={value.draft.dateFrom || undefined}
          onChange={event => onChange({ ...value, draft: { ...value.draft, dateTo: event.target.value } })}
          type="date"
          value={value.draft.dateTo}
        />
      </label>
      <div className={classes.actions ?? 'row-actions'}>
        <button aria-busy={busy} className={classes.button} disabled={busy} type="submit">
          {busy ? t.loading : t.search}
        </button>
        <button
          className={classes.button ?? 'subtle'}
          onClick={onClear ?? (() => onChange(emptyArchiveSearch()))}
          type="button"
        >
          {t.clear}
        </button>
      </div>
    </form>
  )
}

export function ArchiveSearchFeedback({
  status,
  locale = 'zh',
  className,
  count
}: {
  locale?: string
  className?: string
  status?: ApiSchemas['SearchStatus']
  count: number
}): React.JSX.Element {
  const t = archiveSearchCopy(locale)

  return (
    <div aria-live="polite" className={className}>
      {status?.indexing && <p>{t.indexing}</p>}
      {status?.mode === 'keyword' && <p>{t.keyword}</p>}
      {count === 0 && <p>{t.empty}</p>}
      {count >= 20 && <p>{t.limit}</p>}
    </div>
  )
}
