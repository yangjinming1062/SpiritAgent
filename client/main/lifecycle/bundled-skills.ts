import fs from 'node:fs'
import path from 'node:path'

import type { App } from 'electron'

import { errorMessage } from '../shared/utils'

/** 与安装器 install-skills 阶段共用的退出标记：存在时不释放随包技能。 */
const OPT_OUT_MARKER = '.no-bundled-skills'
/** 最近一次完整同步随包技能的桌面版本；复制全部成功后才写入，失败时下次启动重试。 */
const SYNCED_VERSION_MARKER = '.bundled-skills-version'
const MAX_LOGGED_FAILURES = 5

interface BundledSkillsSyncOptions {
  app: Pick<App, 'getVersion' | 'isPackaged'>
  log: (chunk: string) => void
  /** 打包后的 resources 目录，随包技能由 electron-builder extraResources 放在其下 `skills/`。 */
  resourcesPath: string
  spiritagentHome: string
}

/**
 * 桌面版本与上次同步不同时，把随包技能复制到 `$SPIRITAGENT_HOME/skills`：同名文件覆盖、其余内容保留，
 * 存在退出标记时整体跳过，语义与安装器 install-skills 阶段一致。未打包运行不同步。
 * 同步执行，调用方须在 Runner 自动启动与技能索引读取前调用；失败只记日志，不阻断启动。
 */
export function syncBundledSkills({ app, log, resourcesPath, spiritagentHome }: BundledSkillsSyncOptions): void {
  if (!app.isPackaged) {
    return
  }

  if (fs.existsSync(path.join(spiritagentHome, OPT_OUT_MARKER))) {
    log(`[skills] bundled skills sync skipped: ${OPT_OUT_MARKER} present`)

    return
  }

  const version = app.getVersion()
  const markerPath = path.join(spiritagentHome, SYNCED_VERSION_MARKER)

  if (readSyncedVersion(markerPath) === version) {
    return
  }

  const failures: string[] = []
  copyTree(path.join(resourcesPath, 'skills'), path.join(spiritagentHome, 'skills'), failures)

  if (failures.length > 0) {
    log(
      `[skills] bundled skills sync for ${version} incomplete, will retry on next start ` +
        `(${failures.length} failed): ${failures.slice(0, MAX_LOGGED_FAILURES).join('; ')}`
    )

    return
  }

  try {
    fs.writeFileSync(markerPath, version)
    log(`[skills] bundled skills synced for ${version}`)
  } catch (error) {
    log(`[skills] bundled skills copied but ${SYNCED_VERSION_MARKER} not saved: ${errorMessage(error)}`)
  }
}

function readSyncedVersion(markerPath: string): null | string {
  try {
    return fs.readFileSync(markerPath, 'utf8').trim()
  } catch {
    return null
  }
}

/** 逐项复制并收集失败，单项失败不中断其余文件；不删除目标中多出的内容。 */
function copyTree(source: string, target: string, failures: string[]): void {
  let entries: fs.Dirent[]

  try {
    entries = fs.readdirSync(source, { withFileTypes: true })
    fs.mkdirSync(target, { recursive: true })
  } catch (error) {
    failures.push(errorMessage(error))

    return
  }

  for (const entry of entries) {
    const from = path.join(source, entry.name)
    const to = path.join(target, entry.name)

    if (entry.isDirectory()) {
      copyTree(from, to, failures)

      continue
    }

    try {
      fs.copyFileSync(from, to)
    } catch (error) {
      failures.push(errorMessage(error))
    }
  }
}
