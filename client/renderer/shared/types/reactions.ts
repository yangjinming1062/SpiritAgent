export type ReactionBucket = 'poke-light' | 'poke-medium' | 'poke-heavy' | 'drag'

export interface ReactionEntry {
  id: string
  tags: string[]
  bucket: ReactionBucket
  text: string
}
