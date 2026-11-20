import { useCallback, useRef, useState } from 'react'

interface Page<Row> {
  rows: Row[]
  next_cursor: string | null
}

type Library<Row> = Page<Row> & { query: string }
const EMPTY_ROWS: never[] = []

/** 刷新重新读取已展开的页数，整段成功后再一起替换条目和游标。 */
export function useLibraryPages<Row>(query: string, pageSize: number, group = '') {
  const [groups, setGroups] = useState<Record<string, Library<Row>>>({})
  const latest = useRef(groups)
  const visible = groups[group]?.query === query ? groups[group] : undefined

  const read = useCallback(
    async (append: boolean, fetchPage: (cursor: string | undefined, limit: number) => Promise<Page<Row>>) => {
      const saved = latest.current[group]
      const previous = saved?.query === query ? saved : undefined
      if (append && previous && !previous.next_cursor) {
        return previous
      }

      const rows = append && previous ? [...previous.rows] : []
      let cursor = append ? (previous?.next_cursor ?? null) : null
      const pages = append ? 1 : Math.max(1, Math.ceil((previous?.rows.length ?? 0) / pageSize))
      let remaining = pages * pageSize
      do {
        const limit = Math.min(remaining, 100)
        const page = await fetchPage(cursor ?? undefined, limit)
        rows.push(...page.rows)
        cursor = page.next_cursor
        remaining -= limit
      } while (cursor && remaining > 0)
      return { query, rows, next_cursor: cursor }
    },
    [group, pageSize, query]
  )

  const save = useCallback(
    (page: Library<Row>) => {
      latest.current = { ...latest.current, [group]: page }
      setGroups(latest.current)
    },
    [group]
  )

  return {
    rows: visible?.rows ?? EMPTY_ROWS,
    hasMore: Boolean(visible?.next_cursor),
    loaded: Boolean(visible),
    read,
    save
  }
}
