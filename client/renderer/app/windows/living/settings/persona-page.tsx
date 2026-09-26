import { useStore } from '@nanostores/react'
import type React from 'react'

import { $activeAvatarId } from '@/modules/character'
import { cn } from '@/shared/lib/utils'
import { SECTION_TITLE, SettingsSectionIntro } from '@/shared/panel'
import { useStrings } from '@/shared/strings'

import { CharacterCardSection } from './character-card-section'
import { MediaReviewQueue } from './media-review-queue'
import { MemorySection } from './memory-section'
import { PersonaSection } from './persona-editor'

// 角色与记忆同页：编辑人设和角色卡、浏览及修正长期记忆。
// 长页（living-settings）的内嵌段，不使用 SettingsPage 外壳。
export function PersonaPage(): React.ReactElement {
  const dict = useStrings()
  const t = dict.settings.persona

  const avatarId = useStore($activeAvatarId)

  return (
    <div className="space-y-6">
      <SettingsSectionIntro hint={t.intro} title={t.title} />
      <PersonaSection />
      {avatarId != null && <CharacterCardSection avatarId={avatarId} key={`card-${avatarId}`} />}
      {avatarId != null && <MediaReviewQueue />}

      <section>
        <p className={cn(SECTION_TITLE, 'mb-2')}>{t.sectionMemory}</p>
        <MemorySection />
      </section>
    </div>
  )
}
