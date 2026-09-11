import { useStore } from '@nanostores/react'
import type React from 'react'
import { useState } from 'react'

import { $persona } from '@/modules/character'
import { useGatewayRequest } from '@/shared'
import { cn } from '@/shared/lib/utils'
import { BTN_SUBTLE, HINT_TEXT, SECTION_TITLE, SettingsSectionIntro } from '@/shared/panel'
import { notifyError } from '@/shared/store/notifications'
import { useStrings } from '@/shared/strings'

import { MemorySection } from './memory-section'
import { PersonaSection } from './persona-editor'
import { PersonaRetune } from './persona-retune'

interface RetuneInitial {
  name: string
  personality: string
  speaking_style: string
  relationship: string
  user_call_name: string
  user_gender: string
  user_age_bucket: string
  user_hobbies: string
  user_freeform: string
}

// 角色与记忆同页：人设的表单 / 对话式两条编辑路径 + 长期记忆浏览修正。
// 长页（living-settings）的内嵌段，不使用 SettingsPage 外壳。
export function PersonaPage(): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.persona

  const persona = useStore($persona)
  const { requestGateway } = useGatewayRequest()
  const [retuneOpen, setRetuneOpen] = useState(false)
  const [retuneInitial, setRetuneInitial] = useState<RetuneInitial | null>(null)

  // 在弹出向导之前从后端水合 user_* 步骤；拉不到就拒绝打开——
  // 用空值兜底会引导向导把空串 PUT 回去，悄无声息抹掉已保存的个人资料。
  const openRetune = async (): Promise<void> => {
    if (retuneOpen) {
      return
    }

    setRetuneOpen(true)

    try {
      const profile = (await requestGateway<Record<string, string>>('companion.get_user_profile', {})) ?? {}

      setRetuneInitial({
        name: persona?.name ?? '',
        personality: persona?.personality ?? '',
        speaking_style: persona?.speakingStyle ?? '',
        relationship: persona?.relationship ?? '',
        user_call_name: profile.user_call_name ?? '',
        user_gender: profile.user_gender ?? '',
        user_age_bucket: profile.user_age_bucket ?? '',
        user_hobbies: profile.user_hobbies ?? '',
        user_freeform: profile.user_freeform ?? ''
      })
    } catch (err) {
      setRetuneOpen(false)
      notifyError(err, t.retuneLoadFailed)
    }
  }

  return (
    <>
      <div className="space-y-6">
        <SettingsSectionIntro hint={t.intro} title={t.title} />
        <PersonaSection />

        {persona?.name && (
          <section>
            <button className={BTN_SUBTLE} onClick={() => void openRetune()} type="button">
              {t.retuneAction}
            </button>
            <p className={cn(HINT_TEXT, 'mt-1.5')}>{t.retuneHint}</p>
          </section>
        )}

        <section>
          <p className={cn(SECTION_TITLE, 'mb-2')}>{t.sectionMemory}</p>
          <MemorySection />
        </section>
      </div>

      {retuneOpen && persona?.name && retuneInitial && (
        <PersonaRetune initial={retuneInitial} onClose={() => setRetuneOpen(false)} />
      )}
    </>
  )
}
