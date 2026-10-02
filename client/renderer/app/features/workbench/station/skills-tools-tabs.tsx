import { useCallback } from 'react'

import { useRouteEnumParam } from '@/shared/hooks/use-route-enum-param'
import { triggerHaptic } from '@/shared/lib/haptics'
import { Command, Sparkles } from '@/shared/lib/icons'
import { CapsuleTabs } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { SkillsPage } from './skills-page'
import { ToolsetsPage } from './toolsets-page'

const SUBTABS = ['skills', 'toolsets'] as const
type SkillsToolsSubtab = (typeof SUBTABS)[number]

export function SkillsToolsTabs(): React.JSX.Element {
  const t = useStrings()
  const [subtab, setSubtab] = useRouteEnumParam<SkillsToolsSubtab>('subtab', SUBTABS, 'skills')

  const handleSubtabChange = useCallback(
    (next: SkillsToolsSubtab) => {
      if (next !== subtab) {
        triggerHaptic()
        setSubtab(next)
      }
    },
    [setSubtab, subtab]
  )

  return (
    <div className="space-y-6">
      <div className="flex justify-start">
        <CapsuleTabs<SkillsToolsSubtab>
          ariaLabel={t.settings.skills.title}
          onChange={handleSubtabChange}
          options={[
            { value: 'skills', label: t.skills.tabSkills, icon: Sparkles },
            { value: 'toolsets', label: t.skills.tabToolsets, icon: Command }
          ]}
          size="sm"
          value={subtab}
        />
      </div>
      {subtab === 'skills' ? <SkillsPage /> : <ToolsetsPage />}
    </div>
  )
}
