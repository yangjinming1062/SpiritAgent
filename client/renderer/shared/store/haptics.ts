import { atom } from 'nanostores'

import { persistBoolean, storedBoolean } from '@/shared/lib/storage'

const HAPTICS_MUTED_STORAGE_KEY = 'spiritagent.desktop.hapticsMuted'

export const $hapticsMuted = atom(storedBoolean(HAPTICS_MUTED_STORAGE_KEY, false))

$hapticsMuted.subscribe(muted => persistBoolean(HAPTICS_MUTED_STORAGE_KEY, muted))
