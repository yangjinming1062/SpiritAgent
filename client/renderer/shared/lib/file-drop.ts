import { desktopFilePaths } from '@/shared/platform/desktop'
import { resolveDropPaths } from '@client-shared/drop-paths'

/** 从拖拽或剪贴板 FileList/File[] 解析真实文件系统路径（Electron webUtils）；仅保留有效路径条目，避免把无法解析的 blob/dataURL 注入路径管道。白名单由 preload 的 getPathForFile 内部完成。 */
export function resolveDroppedFiles(fileList: FileList | File[] | null | undefined): string[] {
  const webUtils = desktopFilePaths()

  if (!webUtils) {
    return []
  }

  return resolveDropPaths(Array.from(fileList ?? []), file => webUtils.getPathForFile(file))
}
