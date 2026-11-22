import type { ProfileDrafts } from '@/features/companion'
import { definePersistedAtom } from '@/shared/lib/storage'
import { isCompanionMemoryDraft } from '@client-shared/ipc/ui-state'

import { isRecord } from '../../../../shared/is-record'

export const onboardingProfileDraft = definePersistedAtom<ProfileDrafts>({
  key: 'da.onboarding.profile',
  fallback: {},
  isPersistable: (value): value is ProfileDrafts =>
    isRecord(value) && Object.values(value).every(isCompanionMemoryDraft)
})
