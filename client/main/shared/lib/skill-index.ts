import fs from 'node:fs'
import path from 'node:path'

import type { SkillItem } from '@ipc/contracts'
import yaml from 'yaml'

interface RawSkillItem {
  category: string
  compatible: boolean
  description?: string
  name: string
  platforms?: string[] | null
}

// 主进程为每个 skill 计算 `compatible`；渲染端没有
// `process.platform`，用这个标志隐藏不匹配的行。
const HOST_PLATFORM: string = (() => {
  switch (process.platform) {
    case 'darwin':
      return 'macos'

    case 'win32':
      return 'windows'

    default:
      return process.platform
  }
})()

const PLATFORM_ALIASES: Record<string, string> = {
  darwin: 'macos',
  macos: 'macos',
  win32: 'windows',
  windows: 'windows'
}

function platformMatches(declared?: null | string | string[]): boolean {
  if (declared == null) {
    return true
  }

  // YAML 标量（`platforms: macos`）或单元素列表（`platforms: [macos]`）。
  const list = Array.isArray(declared) ? declared : [declared]

  if (list.length === 0) {
    return true
  }

  const mapped = list.map(p => PLATFORM_ALIASES[String(p).toLowerCase()] || String(p).toLowerCase())

  return mapped.includes(HOST_PLATFORM)
}

function listSkillsFromDisk(skillsRoot?: null | string): RawSkillItem[] {
  if (!skillsRoot) {
    return []
  }

  const skills: RawSkillItem[] = []

  try {
    const categories = fs
      .readdirSync(skillsRoot, { withFileTypes: true })
      .filter(e => e.isDirectory() || e.isSymbolicLink())

    for (const category of categories) {
      const categoryPath = path.join(skillsRoot, category.name)

      const skillDirs = fs
        .readdirSync(categoryPath, { withFileTypes: true })
        .filter(e => e.isDirectory() || e.isSymbolicLink())

      for (const skillDir of skillDirs) {
        const skillPath = path.join(categoryPath, skillDir.name)
        const mdPath = path.join(skillPath, 'SKILL.md')

        if (fs.existsSync(mdPath)) {
          let name = skillDir.name
          let description = ''
          let platforms: null | string[] = null

          try {
            const content = fs.readFileSync(mdPath, 'utf8')
            const match = content.match(/^---\s*[\r\n]+([\s\S]*?)[\r\n]+---/)

            if (match) {
              const frontmatter = yaml.parse(match[1])

              if (frontmatter.name) {
                name = frontmatter.name
              }

              if (frontmatter.description) {
                description = frontmatter.description
              }

              if (frontmatter.platforms != null) {
                const raw = frontmatter.platforms
                platforms = (Array.isArray(raw) ? raw : [raw]).map(String)
              }
            }
          } catch {
            // 忽略解析错误
          }

          skills.push({
            category: category.name,
            compatible: platformMatches(platforms),
            description,
            name,
            platforms
          })
        }
      }
    }
  } catch {
    // 忽略
  }

  return skills.sort((a, b) => {
    if (a.category !== b.category) {
      return a.category.localeCompare(b.category)
    }

    return a.name.localeCompare(b.name)
  })
}

// 显式列出字段（而不是 `...skill`），
// 避免 listSkillsFromDisk 内部新增的字段泄漏到渲染端。
function projectSummary(skill: RawSkillItem, disabledSet: Set<string>): SkillItem {
  return {
    category: skill.category,
    compatible: skill.compatible,
    description: skill.description,
    enabled: !disabledSet.has(skill.name),
    name: skill.name,
    platforms: skill.platforms
  }
}

export function buildSkillSummaries(skillsRoot: null | string | undefined, disabledSet: Set<string>): SkillItem[] {
  return listSkillsFromDisk(skillsRoot).map(skill => projectSummary(skill, disabledSet))
}
