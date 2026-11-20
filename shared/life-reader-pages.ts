const PAGE_SIZE = 20

interface Page<T> {
  items: T[]
  next_cursor: string | null
}

export interface ReadPages<T> extends Page<T> {
  page: number
  buffered: T[]
}

// 游标属于服务端整页。跨过阅读边界的那一页只展示前半部分，余项留到用户继续翻页时读取。
export async function readPages<T extends { id: string }>(
  page: number,
  fetchPage: (cursor: string | undefined, limit: number) => Promise<Page<T>>,
  previous: ReadPages<T> | null,
  current: () => boolean,
  boundary?: { page: number; compare: (item: T) => number }
): Promise<ReadPages<T>> {
  const append = previous && previous.page < page ? previous : null
  const items = new Map(append?.items.map(item => [item.id, item]))
  let cursor = append?.next_cursor ?? null
  let more = !append || cursor !== null
  let buffered = append?.buffered ?? []

  async function fetchNext(limit: number) {
    const next = await fetchPage(cursor ?? undefined, limit)
    cursor = next.next_cursor
    more = cursor !== null
    return next.items
  }

  if (!append && boundary) {
    while (more && current()) {
      const batch = await fetchNext(PAGE_SIZE)
      const firstOlder = batch.findIndex(item => boundary.compare(item) > 0)
      const visible = firstOlder < 0 ? batch : batch.slice(0, firstOlder)
      for (const item of visible) {
        items.set(item.id, item)
      }
      if (firstOlder >= 0) {
        buffered = batch.slice(firstOlder)
        break
      }
      const last = batch.at(-1)
      if (last && boundary.compare(last) === 0) {
        break
      }
    }
  }

  // 已读经历全部撤回时，从尚可查看的第一条重新展开。
  const loadedPage = items.size ? (append?.page ?? boundary?.page ?? -1) : -1
  let remaining = (page - loadedPage) * PAGE_SIZE
  while (remaining > 0 && (buffered.length || more) && current()) {
    const batch = buffered.length ? buffered : await fetchNext(Math.min(remaining, PAGE_SIZE))
    const count = Math.min(remaining, batch.length)
    for (const item of batch.slice(0, count)) {
      items.set(item.id, item)
    }
    buffered = batch.slice(count)
    remaining -= count
  }
  return { page, items: [...items.values()], buffered, next_cursor: cursor }
}
