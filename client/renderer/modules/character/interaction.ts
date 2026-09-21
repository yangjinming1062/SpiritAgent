import { $personalityTags } from './persona-store'
import { pickReaction, playReactionAudio } from './reactions/reaction-audio'

export function handleDragEndInteraction(): void {
  const entry = pickReaction('drag', $personalityTags.get())

  void playReactionAudio(entry)
}
