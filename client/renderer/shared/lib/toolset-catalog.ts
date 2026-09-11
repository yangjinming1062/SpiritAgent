import {
  Brain,
  Clock,
  Command,
  Cpu,
  Eye,
  FileText,
  Globe,
  type IconComponent,
  ImageIcon,
  Monitor,
  Search,
  Send,
  Sparkles,
  Terminal,
  Users
} from './icons'

// 渲染层使用的工具集目录。id 权威枚举见 docs/PROTOCOL.md §2.2，此处只挂图标。

export interface ToolsetCatalogEntry {
  id: string
  icon: IconComponent
}

export const TOOLSET_CATALOG: readonly ToolsetCatalogEntry[] = [
  { id: 'browser_automation', icon: Globe },
  { id: 'file_operations', icon: FileText },
  { id: 'terminal', icon: Terminal },
  { id: 'code_execution', icon: Command },
  { id: 'process_management', icon: Cpu },
  { id: 'skills_system', icon: Sparkles },
  { id: 'memory', icon: Brain },
  { id: 'web_tools', icon: Search },
  { id: 'image_generation', icon: ImageIcon },
  { id: 'messaging', icon: Send },
  { id: 'scheduled_tasks', icon: Clock },
  { id: 'agent_delegation', icon: Users },
  { id: 'computer_use', icon: Monitor },
  { id: 'media_analysis', icon: Eye }
] as const
