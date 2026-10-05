// 斜杠命令元数据：权威源在服务端注册表，经 RPC 拉到本窗口；本地只用于自动补全与确认弹窗，dispatch 仍以服务端为准。

import { atom } from 'nanostores'

import { errorMessage } from '@/shared/lib/ipc-error'
import { log } from '@/shared/lib/log'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { $gateway } from '@/shared/store/gateway'

export interface SlashCommandMeta {
  /** 主名（小写，无前导 /）。 */
  name: string
  /** 别名数组（命中同一命令）。 */
  aliases: readonly string[]
  /** 给自动补全弹层与 /帮助 展示的描述。 */
  description: string
  /** 是否需要前端二次确认弹窗（固定预设对话尤其重要）。 */
  requiresConfirmation: boolean
}

// 服务端 ``command.list`` 返回的原始条目。
interface ServerCommandEntry {
  name: string
  aliases?: readonly string[]
  description?: string
  requires_confirmation?: boolean
}

// 启动前与账户清理后为空；拉取失败也保持空，由下次连通或再次进入命令模式时重试。
export const $slashCommandMeta = atom<readonly SlashCommandMeta[]>([])

registerStorageClearHandler(() => $slashCommandMeta.set([]))

let slashMetaInflight: Promise<void> | null = null

function normalizeServerEntry(entry: ServerCommandEntry): SlashCommandMeta {
  return {
    name: entry.name,
    aliases: entry.aliases ?? [],
    description: entry.description ?? '',
    requiresConfirmation: Boolean(entry.requires_confirmation)
  }
}

/** 按名（已剥离前导 /，小写）查 SlashCommandMeta；atom 未加载时返回 undefined。 */
function getLocalSlashMeta(name: string): SlashCommandMeta | undefined {
  return $slashCommandMeta.get().find(cmd => cmd.name === name || cmd.aliases.includes(name))
}

/** 列出所有命令（无别名重复），按 name 排序。 */
function listLocalSlashCommands(): SlashCommandMeta[] {
  return $slashCommandMeta.get().toSorted((a, b) => a.name.localeCompare(b.name))
}

interface ParsedSlashInput {
  /** 命中命令的元数据；未识别时为 undefined。 */
  command: SlashCommandMeta | undefined
  /** 命中的主名（已剥离前导 /，小写）。未识别时为原始 token。 */
  name: string
  /** 命中的参数数组。 */
  args: string[]
}

/** 解析用户输入：`/foo a b` 命中命令；`//注释` 与 `/path/to/file` 不视为命令（首 token 须以 ASCII 字母或中文 U+4E00–U+9FFF 开头）。 */
export function parseSlashInput(rawText: string): ParsedSlashInput | null {
  const trimmed = rawText.trim()

  if (!/^\/[A-Za-z\u4e00-\u9fff]/.test(trimmed)) {
    return null
  }

  const [first, ...args] = trimmed.slice(1).split(/\s+/)
  const name = first.toLowerCase()

  return {
    command: getLocalSlashMeta(name),
    name,
    args
  }
}

/** 模糊打分：完全等于 100，前缀按命中长度递减，子序列按距离得分；返回 0 表示不匹配。 */
function fuzzyScore(query: string, target: string): number {
  if (!query) {
    return 100
  } // 空查询 → 全量

  const q = query.toLowerCase()
  const t = target.toLowerCase()

  if (t === q) {
    return 100
  }

  if (t.startsWith(q)) {
    return 80 - (t.length - q.length) * 2
  }

  // 子序列：q 的字符按顺序出现在 t 中
  let qi = 0
  let lastMatch = -1
  let score = 0

  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      score += ti - lastMatch === 1 ? 10 : 4 // 连续命中加权
      lastMatch = ti
      qi++
    }
  }

  if (qi < q.length) {
    return 0
  }

  return score
}

export interface ScoredSlashCommand {
  cmd: SlashCommandMeta
  score: number
  /** 实际匹配上的展示 token（name 或 alias），用于弹层显示。 */
  matchedKey: string
}

/** 按 query 模糊过滤命令列表，按得分降序；主名+别名+description 取最高分。 */
export function fuzzyFilterCommands(query: string, limit = 8): ScoredSlashCommand[] {
  const cmds = listLocalSlashCommands()

  if (!query) {
    return cmds.map(cmd => ({ cmd, score: 100, matchedKey: cmd.name }))
  }

  const scored: ScoredSlashCommand[] = []

  for (const cmd of cmds) {
    let best: { score: number; key: string } | null = null

    for (const key of [cmd.name, ...cmd.aliases]) {
      const s = fuzzyScore(query, key)

      if (s > 0 && (!best || s > best.score)) {
        best = { score: s, key }
      }
    }

    const descScore = fuzzyScore(query, cmd.description) * 0.5

    if (descScore > 0 && (!best || descScore > best.score)) {
      best = { score: descScore, key: cmd.description }
    }

    if (best) {
      scored.push({ cmd, score: best.score, matchedKey: best.key })
    }
  }

  scored.sort((a, b) => b.score - a.score || a.cmd.name.localeCompare(b.cmd.name))

  return scored.slice(0, limit)
}

interface SlashCommandListResponse {
  commands: readonly ServerCommandEntry[]
}

/** 从网关拉取命令元数据写入本窗口 atom；已有数据则跳过，失败保持空以便重试；返回前网关已替换或账户已清理时丢弃结果。 */
export async function fetchSlashCommandMeta(): Promise<void> {
  if ($slashCommandMeta.get().length > 0) {
    return
  }

  if (slashMetaInflight) {
    return slashMetaInflight
  }

  const gateway = $gateway.get()

  if (!gateway) {
    return
  }

  const epoch = currentClearEpoch()

  slashMetaInflight = (async () => {
    try {
      const res = await gateway.request<SlashCommandListResponse>('command.list', {})

      if ($gateway.get() === gateway && currentClearEpoch() === epoch) {
        $slashCommandMeta.set((res.commands ?? []).map(normalizeServerEntry))
      }
    } catch (error) {
      const msg = errorMessage(error)
      log.error('slash-commands', `command.list failed: ${msg}`, error)
    } finally {
      slashMetaInflight = null
    }
  })()

  return slashMetaInflight
}
