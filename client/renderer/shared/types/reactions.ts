export type ReactionBucket = 'drag'

export interface ReactionEntry {
  id: string
  tags: string[]
  bucket: ReactionBucket
  text: string
}
