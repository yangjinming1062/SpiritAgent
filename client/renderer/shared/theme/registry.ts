import type { SpiritAgentUiTheme } from '@ipc/contracts'

// 主题元数据注册表——UI 色值的权威在 styles.css 的 html[data-palette]/[data-effect] 变量块；
// 这里只存 CSS 存不了的：展示名与说明。预览色板由主题 id 驱动 CSS 类。
export interface ThemeDefinition {
  description: string
  id: SpiritAgentUiTheme
  label: string
}

export const THEMES: readonly ThemeDefinition[] = [
  {
    description: '深邃沉静，适合夜间使用',
    id: 'night',
    label: '夜色'
  },
  {
    description: '温暖明亮，充满活力',
    id: 'day',
    label: '日色'
  },
  {
    description: '暗底清透，房间从窗壳透出',
    id: 'night-clear',
    label: '夜色透明'
  },
  {
    description: '亮底清透，房间从窗壳透出',
    id: 'day-clear',
    label: '日色透明'
  }
]
