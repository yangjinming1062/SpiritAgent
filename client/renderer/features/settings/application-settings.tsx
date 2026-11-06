import { useStore } from '@nanostores/react'
import type React from 'react'

import { usePanelAction } from '@/shared/hooks/use-panel-action'
import { runnerApi } from '@/shared/platform/desktop'
import { $locale } from '@/shared/store/locale'
import { useStrings } from '@/shared/strings'
import { PanelSelect, SettingCard, SettingRow, SettingsSectionIntro } from '@/ui'

/** 应用语言：写入 Runner 配置后由云端广播带动渲染层切换。 */
export function ApplicationSettings(): React.JSX.Element {
  const locale = useStore($locale)
  const strings = useStrings()
  const { run, busy: savingLanguage } = usePanelAction()

  const changeLanguage = (value: string): void => {
    void run(
      async () => {
        const result = await runnerApi().runnerConfig.patch({ path: ['language'], value })

        if (!result.ok) {
          throw new Error(result.error)
        }
      },
      { failure: strings.settings.application.languageSaveFailed }
    )
  }

  return (
    <div className="space-y-5">
      <SettingsSectionIntro hint={strings.settings.application.hint} title={strings.settings.application.title} />
      <SettingCard>
        <SettingRow label={strings.settings.application.languageLabel}>
          <PanelSelect
            ariaLabel={strings.settings.application.languageLabel}
            disabled={savingLanguage}
            onChange={changeLanguage}
            options={[
              { value: 'zh', label: '简体中文' },
              { value: 'en', label: 'English' }
            ]}
            value={locale}
          />
        </SettingRow>
      </SettingCard>
    </div>
  )
}
