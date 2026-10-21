import { realpath, stat } from 'node:fs/promises'
import path from 'node:path'

import { desktopMacos, type MacosApplicationInfo } from '../shared/desktop-macos'

export function isMacosApplicationTarget(target: string): boolean {
  return path.isAbsolute(target) && path.extname(target).toLowerCase() === '.app'
}

export async function validateMacosApplicationTarget(target: string): Promise<MacosApplicationInfo> {
  if (!isMacosApplicationTarget(target)) {
    throw new Error('请选择应用程序。')
  }

  const canonical = await realpath(target)

  if (
    !isMacosApplicationTarget(canonical) ||
    !(await stat(canonical)).isDirectory() ||
    !(await stat(path.join(canonical, 'Contents', 'Info.plist'))).isFile()
  ) {
    throw new Error('应用程序目录无效。')
  }

  // 原生模块还会核验 Bundle 的可执行文件，不能仅凭 .app 后缀授予启动能力。
  const [application] = await desktopMacos().applicationInfo([canonical], false)

  if (!application || application.path !== canonical) {
    throw new Error('应用程序缺少有效的启动文件。')
  }

  return application
}

export async function readMacosApplicationIcon(target: string): Promise<string | null> {
  const [application] = await desktopMacos().applicationInfo([target], true)

  return application?.path === target ? application.icon : null
}
