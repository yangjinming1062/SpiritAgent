export async function saveWithReadback<T>({
  write,
  read,
  matches,
  isCurrent,
  onUnconfirmed
}: {
  write: () => Promise<T>
  read: () => Promise<T | null>
  matches: (latest: T) => boolean
  isCurrent: () => boolean
  onUnconfirmed: (latest: T | null | undefined) => void | Promise<void>
}): Promise<T> {
  try {
    return await write()
  } catch (failure) {
    if (!isCurrent()) {
      throw failure
    }
    let latest: T | null | undefined
    try {
      latest = await read()
      if (isCurrent() && latest !== null && matches(latest)) {
        return latest
      }
    } catch {
      // 核对失败仍保留原始写入错误。
    }
    if (isCurrent()) {
      try {
        await onUnconfirmed(latest)
      } catch {
        // 刷新失败也不替换原始写入错误。
      }
    }
    throw failure
  }
}
