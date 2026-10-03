import type { ToolsetItem } from '@ipc/contracts'

interface ToolsetDef {
  extraTools?: string[]
  id: string
  prefixes?: string[]
  staticTools?: string[]
}

// 工具集 id 的权威枚举（覆盖 Runner 侧与 Backend 桶的全部 id）：Runner 侧按 schema/前缀动态匹配，Backend 侧登记静态工具名；id → 工具名映射见 runner/tools/toolsets/catalog.py 与 backend/services/infrastructure/tool_runtime/toolsets.py。
const TOOLSET_DEFS: ToolsetDef[] = [
  { id: 'browser_automation', prefixes: ['browser_'] },
  { extraTools: ['read_file', 'write_file', 'patch', 'list_directory', 'search_files'], id: 'file_operations' },
  { extraTools: ['terminal'], id: 'terminal' },
  { extraTools: ['execute_code'], id: 'code_execution' },
  { extraTools: ['process'], id: 'process_management' },
  { extraTools: ['skills_list', 'skill_view', 'skill_manage'], id: 'skills_system' },
  { id: 'memory', staticTools: ['memory_retain', 'memory_recall', 'memory_inspect'] },
  { id: 'posts', staticTools: ['post_publish', 'post_status'] },
  { id: 'web_tools', staticTools: ['web_search', 'web_extract'] },
  {
    id: 'image_generation',
    staticTools: [
      'image_generate',
      'media_inspect',
      'image_regenerate',
      'scene_list',
      'scene_get',
      'scene_create',
      'scene_activate'
    ]
  },
  { id: 'messaging', staticTools: ['send_message_tool'] },
  { id: 'scheduled_tasks', staticTools: ['cronjob', 'companion_wait'] },
  { id: 'agent_delegation', staticTools: ['agent_delegate_tool'] },
  { extraTools: ['computer_use'], id: 'computer_use' },
  { extraTools: ['vision_analyze'], id: 'media_analysis' },
  {
    extraTools: [
      'system.get_idle_seconds',
      'system.is_screen_locked',
      'system.get_focused_app',
      'system.is_fullscreen',
      'system.snapshot',
      'system.get_power_state',
      'system.get_windows',
      'system.open_application',
      'system.get_work_area',
      'system.get_cursor_pos',
      'system.click_at'
    ],
    id: 'system_awareness'
  }
]

// Set 保持首次加入的顺序并去重：静态工具无条件列出，前缀与额外工具只取 Runner 实际提供的。
function toolNamesForToolset(def: ToolsetDef, availableNames: Set<string>): string[] {
  const names = new Set(def.staticTools)

  for (const prefix of def.prefixes ?? []) {
    for (const name of availableNames) {
      if (name.startsWith(prefix)) {
        names.add(name)
      }
    }
  }

  for (const name of def.extraTools ?? []) {
    if (availableNames.has(name)) {
      names.add(name)
    }
  }

  return [...names]
}

export function buildToolsetRoster(schemas: Array<{ name?: string }>, disabledToolsetIds: Set<string>): ToolsetItem[] {
  const availableNames = new Set(schemas.map(s => s?.name).filter(Boolean) as string[])

  return TOOLSET_DEFS.map(def => ({
    enabled: !disabledToolsetIds.has(def.id),
    id: def.id,
    toolNames: toolNamesForToolset(def, availableNames)
  }))
}
