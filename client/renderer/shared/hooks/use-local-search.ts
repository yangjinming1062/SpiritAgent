import { useState } from 'react'

export interface LocalSearch {
  query: string
  searching: boolean
  onQueryChange: (next: string) => void
  matches: (...values: (string | null | undefined)[]) => boolean
}

/** 列表本地筛选：输入即生效，大小写不敏感；查询词归调用方页面持有。 */
export function useLocalSearch(): LocalSearch {
  const [query, setQuery] = useState('')
  const term = query.trim().toLocaleLowerCase()

  return {
    query,
    searching: term.length > 0,
    onQueryChange: (next: string): void => setQuery(next),
    matches: (...values: (string | null | undefined)[]): boolean =>
      !term || values.some(value => value?.toLocaleLowerCase().includes(term))
  }
}
