import { type SetStateAction, useCallback, useMemo } from 'react'

import { emptyArchiveSearch, isArchiveSearchState } from '../shared/archive-search'
import { LifeReader } from '../shared/life-reader'
import {
  initialLifeReader,
  isLifeReaderState,
  type LifeReaderState,
  type PursuitFilter,
  pursuitFilters,
  restoreLifeReader
} from '../shared/life-reader-state'
import type { ReadLife } from '../shared/use-life-reader-data'

import type { RemoteApi } from './api'
import { LifeResultPreviews } from './LifeResults'
import { usePageState } from './page-state'

const scrollContainer = (root: HTMLElement) => root.closest<HTMLElement>('main.content')

export function LifeSummary({ api, refresh }: { api: RemoteApi; refresh: number }) {
  const [oldTopic] = usePageState<string | null>('life.topic', null)
  const [oldSearch] = usePageState('life.search', emptyArchiveSearch())
  const [oldStatus] = usePageState<PursuitFilter>('life.status', 'active')
  const [saved, save] = usePageState(
    'life.reader',
    restoreLifeReader({
      topicId: oldTopic,
      search: isArchiveSearchState(oldSearch) ? oldSearch : undefined,
      status: pursuitFilters.includes(oldStatus) ? oldStatus : undefined
    })
  )
  const state = isLifeReaderState(saved) ? saved : initialLifeReader()
  const setState = useCallback(
    (action: SetStateAction<LifeReaderState>) => {
      save(previous =>
        typeof action === 'function' ? action(isLifeReaderState(previous) ? previous : initialLifeReader()) : action
      )
    },
    [save]
  )
  const read: ReadLife = useMemo(() => api.invoke.bind(api), [api])

  return (
    <LifeReader
      getScroller={scrollContainer}
      locale="zh"
      read={read}
      results={ref => <LifeResultPreviews api={api} reference={ref} />}
      revision={refresh}
      setState={setState}
      state={state}
      statusSelect={({ value, options, onChange, ariaLabel }) => (
        <select
          aria-label={ariaLabel}
          className="life-select"
          onChange={event => {
            const option = options.find(item => item.value === event.target.value)
            if (option) {
              onChange(option.value)
            }
          }}
          value={value}
        >
          {options.map(item => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      )}
    />
  )
}
