import { useStore } from '@nanostores/react'
import type React from 'react'

import { $locale } from '@/shared/store/locale'

import type { ApiSchemas } from '../../../shared/api.generated'
import { archiveSearchCopy, type ArchiveSearchState, emptyArchiveSearch } from '../../../shared/archive-search'

import styles from './archive-search.module.css'
import { BTN_SUBTLE, INPUT_CLASS } from './palette'

export function ArchiveSearchForm({
  value,
  onChange,
  label,
  busy = false
}: {
  value: ArchiveSearchState
  onChange: (value: ArchiveSearchState) => void
  label: string
  busy?: boolean
}): React.JSX.Element {
  const locale = useStore($locale)
  const t = archiveSearchCopy(locale)

  return (
    <form
      aria-label={label}
      className={styles.form}
      onSubmit={event => {
        event.preventDefault()
        onChange({ ...value, applied: { ...value.draft } })
      }}
    >
      <label className={styles.query}>
        <span>{label}</span>
        <input
          className={INPUT_CLASS}
          maxLength={1000}
          onChange={event => onChange({ ...value, draft: { ...value.draft, query: event.target.value } })}
          placeholder={t.query}
          type="search"
          value={value.draft.query}
        />
      </label>
      <label>
        <span>{t.from}</span>
        <input
          className={INPUT_CLASS}
          max={value.draft.dateTo || undefined}
          onChange={event => onChange({ ...value, draft: { ...value.draft, dateFrom: event.target.value } })}
          type="date"
          value={value.draft.dateFrom}
        />
      </label>
      <label>
        <span>{t.to}</span>
        <input
          className={INPUT_CLASS}
          min={value.draft.dateFrom || undefined}
          onChange={event => onChange({ ...value, draft: { ...value.draft, dateTo: event.target.value } })}
          type="date"
          value={value.draft.dateTo}
        />
      </label>
      <div className={styles.actions}>
        <button aria-busy={busy} className={BTN_SUBTLE} disabled={busy} type="submit">
          {busy ? t.loading : t.search}
        </button>
        <button className={BTN_SUBTLE} onClick={() => onChange(emptyArchiveSearch())} type="button">
          {t.clear}
        </button>
      </div>
    </form>
  )
}

export function ArchiveSearchFeedback({
  status,
  count
}: {
  status?: ApiSchemas['SearchStatus']
  count: number
}): React.JSX.Element {
  const t = archiveSearchCopy(useStore($locale))

  return (
    <div aria-live="polite" className={styles.feedback}>
      {status?.indexing && <p>{t.indexing}</p>}
      {status?.mode === 'keyword' && <p>{t.keyword}</p>}
      {count === 0 && <p>{t.empty}</p>}
      {count >= 20 && <p>{t.limit}</p>}
    </div>
  )
}
