import fs from 'node:fs'
import path from 'node:path'

import log from 'electron-log/main'
import yaml from 'yaml'

import type { SkillItem } from '@ipc/contracts'

interface RawSkillItem {
  category: string
  compatible: boolean
  description?: string
  name: string
  platforms?: string[] | null
}

const PLATFORM_ALIASES: Record<string, string> = { darwin: 'macos', win32: 'windows' }

function canonicalPlatform(name: string): string {
  const lower = name.toLowerCase()

  return PLATFORM_ALIASES[lower] ?? lower
}

// 主进程为每个 skill 计算 compatible；渲染端没有 process.platform，用这个标志隐藏不匹配的行。
const HOST_PLATFORM = canonicalPlatform(process.platform)

function platformMatches(platforms: null | string[]): boolean {
  return !platforms?.length || platforms.some(p => canonicalPlatform(p) === HOST_PLATFORM)
}

// 目录缺失（未安装技能、坏链接）是正常状态；其他读取失败记录后跳过，不中断其余分类。
function readSubdirectories(dirPath: string): fs.Dirent[] {
  try {
    return fs.readdirSync(dirPath, { withFileTypes: true }).filter(e => e.isDirectory() || e.isSymbolicLink())
  } catch (error) {
    if ((error as NodeJS.ErrnoException)?.code !== 'ENOENT') {
      log.warn(`[skills] cannot read ${dirPath}:`, error)
    }

    return []
  }
}

function listSkillsFromDisk(skillsRoot?: null | string): RawSkillItem[] {
  if (!skillsRoot) {
    return []
  }

  const skills: RawSkillItem[] = []

  for (const category of readSubdirectories(skillsRoot)) {
    const categoryPath = path.join(skillsRoot, category.name)

    for (const skillDir of readSubdirectories(categoryPath)) {
      const skillPath = path.join(categoryPath, skillDir.name)
      const mdPath = path.join(skillPath, 'SKILL.md')

      if (fs.existsSync(mdPath)) {
        let name = skillDir.name
        let description = ''
        let platforms: null | string[] = null

        try {
          const content = fs.readFileSync(mdPath, 'utf8')
          const match = content.match(/^---\s*[\r\n]+([\s\S]*?)[\r\n]+---/)
          const frontmatter: unknown = match ? yaml.parse(match[1]) : null

          if (frontmatter && typeof frontmatter === 'object') {
            // YAML 标量可能是数字等非字符串；只接受字符串，排序与渲染层都按字符串处理。
            const fields = frontmatter as { description?: unknown; name?: unknown; platforms?: unknown }

            if (typeof fields.name === 'string' && fields.name) {
              name = fields.name
            }

            if (typeof fields.description === 'string' && fields.description) {
              description = fields.description
            }

            if (fields.platforms != null) {
              const raw = fields.platforms
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

  return skills.sort((a, b) => {
    if (a.category !== b.category) {
      return a.category.localeCompare(b.category)
    }

    return a.name.localeCompare(b.name)
  })
}

// 显式列出字段（而不是 `...skill`），避免 listSkillsFromDisk 内部新增的字段泄漏到渲染端。
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
