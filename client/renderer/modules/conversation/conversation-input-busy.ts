import { atom, computed } from 'nanostores'

import { registerStorageClearHandler } from '@/shared/lib/storage'

type InputActivity = 'recording' | 'uploading'
const $activities = atom<{ owner: symbol; activity: InputActivity }[]>([])

export const $conversationInputBusy = computed($activities, activities => ({
  recording: activities.some(item => item.activity === 'recording'),
  uploading: activities.some(item => item.activity === 'uploading')
}))

export function beginConversationInputActivity(activity: InputActivity): () => void {
  const owner = Symbol(activity)
  $activities.set([...$activities.get(), { owner, activity }])

  return () => $activities.set($activities.get().filter(item => item.owner !== owner))
}

registerStorageClearHandler(() => $activities.set([]))
