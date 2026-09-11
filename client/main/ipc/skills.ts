import path from 'node:path'

import { IPC } from '@ipc/contracts'
import type { IpcMain } from 'electron'

import * as store from '../shared/lib/runner-config-store'
import { buildSkillSummaries } from '../shared/lib/skill-index'
import { buildToolsetRoster } from '../shared/lib/toolset-index'

interface SkillsIpcDeps {
  spiritagentHome?: null | string
  getRunnerBridge?: () => { getTools?: () => Record<string, unknown>[] } | null | undefined
  ipcMain: IpcMain
}

// 把"启/禁用某项"的两段相似分支合并——校验字段名、相同字段 enabled、
// 同样在 disabled 集合里增删并写回。差异通过 section / keyField / idField 注入。
async function toggleDisabled({
  section,
  idField,
  idValue,
  enabled
}: {
  section: 'skills' | 'toolsets'
  idField: 'name' | 'id'
  idValue: unknown
  enabled: unknown
}): Promise<{ error: string; ok: false } | { error?: undefined; ok: true }> {
  if (typeof idValue !== 'string' || !idValue) {
    return { error: `invalid ${idField}`, ok: false }
  }

  if (typeof enabled !== 'boolean') {
    return { error: 'invalid enabled', ok: false }
  }

  const current = store.getDisabledSet(section)
  const wasDisabled = current.has(idValue)

  if (wasDisabled === enabled) {
    return { ok: true }
  }

  const result = await store.mutate(config => {
    const slot = (config[section] as { disabled?: unknown } | undefined) ?? {}
    const list = Array.isArray(slot.disabled) ? slot.disabled : []
    const next = new Set(list.map(String))

    if (enabled) {
      next.delete(idValue)
    } else {
      next.add(idValue)
    }

    config[section] = { ...slot, disabled: [...next].sort() }

    return next
  })

  return result.ok ? { ok: true } : { error: (result as { error?: string }).error ?? 'mutate failed', ok: false }
}

export function registerSkillsIpc({ spiritagentHome, getRunnerBridge, ipcMain }: SkillsIpcDeps): void {
  const skillsRoot = path.join(spiritagentHome || '', 'skills')

  const loadToolsetSchemas = (): Record<string, unknown>[] => {
    try {
      return (getRunnerBridge?.()?.getTools?.() as Record<string, unknown>[]) ?? []
    } catch {
      return []
    }
  }

  ipcMain.handle(IPC.invoke.skillsList, () => ({
    ok: true,
    skills: buildSkillSummaries(skillsRoot, store.getDisabledSet())
  }))

  ipcMain.handle(IPC.invoke.skillSetEnabled, async (_evt, payload) => {
    const { enabled, name } = payload ?? {}

    if (enabled === true) {
      const summary = buildSkillSummaries(skillsRoot, store.getDisabledSet()).find(s => s.name === name)

      if (!summary || !summary.compatible) {
        return { error: 'Skill is not available on the current platform.', ok: false }
      }
    }

    const result = await toggleDisabled({ section: 'skills', idField: 'name', idValue: name, enabled })

    if (!result.ok) {
      return result
    }

    return { ok: true, skills: buildSkillSummaries(skillsRoot, store.getDisabledSet()) }
  })

  ipcMain.handle(IPC.invoke.toolsetsList, () => ({
    ok: true,
    toolsets: buildToolsetRoster(loadToolsetSchemas(), store.getDisabledSet('toolsets'))
  }))

  ipcMain.handle(IPC.invoke.toolsetSetEnabled, async (_evt, payload) => {
    const schemas = loadToolsetSchemas()

    const result = await toggleDisabled({
      section: 'toolsets',
      idField: 'id',
      idValue: payload?.id,
      enabled: payload?.enabled
    })

    if (!result.ok) {
      return result
    }

    return { ok: true, toolsets: buildToolsetRoster(schemas, store.getDisabledSet('toolsets')) }
  })
}
