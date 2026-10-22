/**
 * 拖拽或粘贴得到的文件逐个解析真实路径：单个文件解析失败不影响其余条目，空路径丢弃。
 * 路径解析与白名单登记由调用方给出的 pathFor 完成，两侧共用同一入参顺序。
 */
export function resolveDropPaths<T>(files: Iterable<T>, pathFor: (file: T) => string): string[] {
  const paths: string[] = []

  for (const file of files) {
    try {
      const path = pathFor(file)

      if (path) {
        paths.push(path)
      }
    } catch {
      /* 单个文件解析失败不影响其他文件 */
    }
  }

  return paths
}
