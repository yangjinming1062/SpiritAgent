import type { ToolsetItem } from '@ipc/contracts'

interface ToolsetDef {
  extraTools?: string[]
  id: string
  prefixes?: string[]
  staticTools?: string[]
}

// 供主进程生成工具集清单的目录。id 权威枚举见 docs/PROTOCOL.md §2.2；
// Runner 侧工具集在此按 schema/前缀动态匹配，Backend 侧工具集（memory 等）在此登记静态工具名。
const TOOLSET_DEFS: ToolsetDef[] = [
  { id: 'browser_automation', prefixes: ['browser_'] },
  { extraTools: ['read_file', 'write_file', 'patch', 'list_directory', 'search_files'], id: 'file_operations' },
  { extraTools: ['terminal'], id: 'terminal' },
  { extraTools: ['execute_code'], id: 'code_execution' },
  { extraTools: ['process'], id: 'process_management' },
  { extraTools: ['skills_list', 'skill_view', 'skill_manage'], id: 'skills_system' },
  { id: 'memory', staticTools: ['memory_retain', 'memory_recall', 'memory_forget'] },
  { id: 'web_tools', staticTools: ['web_search', 'web_extract'] },
  { id: 'image_generation', staticTools: ['image_generate'] },
  { id: 'messaging', staticTools: ['send_message_tool'] },
  { id: 'scheduled_tasks', staticTools: ['cronjob'] },
  { id: 'agent_delegation', staticTools: ['agent_delegate_tool'] },
  { extraTools: ['computer_use'], id: 'computer_use' },
  { extraTools: ['vision_analyze'], id: 'media_analysis' }
]

function toolNamesForToolset(def: ToolsetDef, availableNames: Set<string>): string[] {
  const names: string[] = []
  const seen = new Set<string>()

  if (def.staticTools) {
    for (const name of def.staticTools) {
      if (!seen.has(name)) {
        names.push(name)
        seen.add(name)
      }
    }
  }

  if (def.prefixes) {
    for (const prefix of def.prefixes) {
      for (const name of availableNames) {
        if (name.startsWith(prefix) && !seen.has(name)) {
          names.push(name)
          seen.add(name)
        }
      }
    }
  }

  if (def.extraTools) {
    for (const name of def.extraTools) {
      if (seen.has(name)) {
        continue
      }

      if (availableNames.has(name)) {
        names.push(name)
        seen.add(name)
      }
    }
  }

  return names
}

// 生成供渲染端使用的工具集清单。供 `spiritagent:toolsets:list` 调用。
export function buildToolsetRoster(schemas: Array<{ name?: string }>, disabledToolsetIds: Set<string>): ToolsetItem[] {
  const availableNames = new Set(schemas.map(s => s?.name).filter(Boolean) as string[])

  return TOOLSET_DEFS.map(def => ({
    enabled: !disabledToolsetIds.has(def.id),
    id: def.id,
    toolNames: toolNamesForToolset(def, availableNames)
  }))
}
