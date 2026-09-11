import os from 'node:os'
import path from 'node:path'

import { spiritagentHome } from '../security/paths'

import * as store from './lib/runner-config-store'
import { buildSkillSummaries } from './lib/skill-index'

interface BuildClientContextOptions {
  arch?: string
  spiritagentHome?: null | string
  desktopVersion?: string
  listSkills?: typeof buildSkillSummaries
  nodeVersion?: null | string
  platform?: string
  release?: string
  skillsRoot?: string
  userAgent?: null | string
}

interface ClientContextResult {
  client_context: {
    environment_hints: string
    platform_hints: string
    skills: string[]
  }
  client_version: string
}

export function buildClientContext(options: BuildClientContextOptions = {}): ClientContextResult {
  const platform = options.platform ?? process.platform
  const arch = options.arch ?? process.arch
  const release = options.release ?? os.release()
  const nodeVersion = options.nodeVersion ?? process.versions?.node ?? null
  const desktopVersion = options.desktopVersion ?? 'unknown'
  const userAgent = options.userAgent ?? null
  const home = options.spiritagentHome ?? spiritagentHome()
  const skillsRoot = options.skillsRoot ?? (home ? path.join(home, 'skills') : '')
  const listSkills = options.listSkills ?? buildSkillSummaries

  const lines = [
    `${platform} ${release}`,
    `arch=${arch}`,
    desktopVersion !== 'unknown' ? `spiritagent-desktop=${desktopVersion}` : null,
    nodeVersion ? `node=${nodeVersion}` : null,
    home ? `spiritagent_home=${home}` : null
  ].filter(Boolean)

  const enabledNames = listSkills(skillsRoot, store.getDisabledSet())
    // 这里过滤掉针对其他 OS 标记的 skill，避免后端
    // "Enabled local skills…" 这段提示词块（system_prompt.py:64-68）
    // 列出实际不可用的 skill。Runner 侧（skill_matches_platform）也有运行时过滤；
    // 这一层是提示词侧的入口。
    .filter(s => s.enabled && s.compatible)
    .map(s => s.name)

  return {
    client_context: {
      environment_hints: lines.join('; '),
      platform_hints: userAgent || `SpiritAgentDesktop/${desktopVersion} (${platform}; ${arch})`,
      skills: enabledNames
    },
    client_version: desktopVersion
  }
}
