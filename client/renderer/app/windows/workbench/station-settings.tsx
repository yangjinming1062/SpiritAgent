// 工作台「工位环境」：顶部分区胶囊切换推理与对话/本机执行器/技能与工具。
// 生活向设置由生活空间托管。
//
// 模块内 tab 状态，不读全局 appSettingsView；
// 初始 tab 从 `#/inference|runner|skills` hash 解析，
// 设置打开期间再收到 hashchange 也会同步切到对应 tab。

import type React from 'react'
import { useEffect, useState } from 'react'

import { PAGE_INSET_X } from '@/shared/layout/page-inset'
import { triggerHaptic } from '@/shared/lib/haptics'
import { normalizeHashPath } from '@/shared/lib/hash-route'
import { Brain, Cpu, type IconComponent, Sparkles } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { CapsuleTabs } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { InferencePage } from './station/inference-page'
import { RunnerPage } from './station/runner-page'
import { SkillsToolsTabs } from './station/skills-tools-tabs'

export type StationSettingsTab = 'inference' | 'runner' | 'skills'

function buildStationNav(tTabs: ReturnType<typeof useStrings>['workbench']['station']['tabs']): ReadonlyArray<{
  icon: IconComponent
  label: string
  value: StationSettingsTab
}> {
  return [
    { icon: Brain, label: tTabs.inference, value: 'inference' },
    { icon: Cpu, label: tTabs.runner, value: 'runner' },
    { icon: Sparkles, label: tTabs.skills, value: 'skills' }
  ]
}

const STATION_TAB_IDS: ReadonlyArray<StationSettingsTab> = ['inference', 'runner', 'skills']

function readHashTab(allowed: ReadonlyArray<StationSettingsTab>): StationSettingsTab {
  if (typeof window === 'undefined' || !window.location.hash) {
    return 'inference'
  }

  const pathOnly = normalizeHashPath(window.location.hash)

  const segment = pathOnly.startsWith('settings/')
    ? pathOnly.slice('settings/'.length)
    : pathOnly.startsWith('station/')
      ? pathOnly.slice('station/'.length)
      : pathOnly

  return (allowed as ReadonlyArray<string>).includes(segment) ? (segment as StationSettingsTab) : 'inference'
}

export function StationSettings(): React.JSX.Element {
  const t = useStrings().workbench.station
  const stationNav = buildStationNav(t.tabs)
  const [activeTab, setActiveTab] = useState<StationSettingsTab>(() => readHashTab(STATION_TAB_IDS))

  // 工作台窗口内 hash 变化时同步切 tab（深链入站 + 设置打开期间被再次定位）。
  useEffect(() => {
    const onHash = (): void => setActiveTab(readHashTab(STATION_TAB_IDS))

    window.addEventListener('hashchange', onHash)

    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const handleSelectTab = (next: StationSettingsTab): void => {
    triggerHaptic('open')
    setActiveTab(next)

    if (typeof window !== 'undefined') {
      window.history.replaceState(null, '', `#/${next}`)
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-transparent">
      <header className="sticky top-0 z-20 flex shrink-0 justify-center px-6 pt-3.5 pb-2.5">
        <div aria-hidden="true" className="settings-header-scrim pointer-events-none absolute inset-0 -bottom-3 z-0" />
        <div className="relative z-10 flex items-center gap-3">
          <CapsuleTabs ariaLabel={t.tabsAria} onChange={handleSelectTab} options={stationNav} value={activeTab} />
        </div>
      </header>

      <div className={cn('min-h-0 flex-1 overflow-y-auto py-5', PAGE_INSET_X)} key={activeTab}>
        <div className="mx-auto w-full max-w-3xl pb-16">
          {activeTab === 'inference' ? <InferencePage /> : null}
          {activeTab === 'runner' ? <RunnerPage /> : null}
          {activeTab === 'skills' ? <SkillsToolsTabs /> : null}
        </div>
      </div>
    </div>
  )
}
