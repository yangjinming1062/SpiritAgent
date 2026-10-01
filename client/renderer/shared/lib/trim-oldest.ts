/** 按插入序淘汰最旧项，直到条目数不超过 max；Map 与 Set 通用。 */
export function trimOldest<K>(entries: Map<K, unknown> | Set<K>, max: number): void {
  for (const key of entries.keys()) {
    if (entries.size <= max) {
      return
    }

    entries.delete(key)
  }
}
