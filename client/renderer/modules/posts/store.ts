import { atom } from 'nanostores'

import { apiSucceeded, authedApi } from '@/shared/lib/authed-api'
import { isRecord } from '@/shared/lib/is-record'
import { currentClearEpoch, registerStorageClearHandler } from '@/shared/lib/storage'
import { createUnreadMirror } from '@/shared/lib/unread-mirror'

export type PostContentType = 'text' | 'image' | 'video' | 'audio'
type ReplyStatus = 'none' | 'pending' | 'running' | 'completed' | 'failed'

export interface PostCommentEntry {
  content: string
  createdAt: string
  updatedAt: string
  id: string
  postId: string
  role: 'user' | 'companion'
  replyToCommentId: string | null
  replyStatus: ReplyStatus
  replyError: string | null
}

interface CommentWire {
  content: string
  created_at: string
  updated_at: string
  id: string
  post_id: string
  role: 'user' | 'companion'
  reply_to_comment_id: string | null
  reply_status: ReplyStatus
  reply_error: string | null
}

export interface PostEntry {
  audioUrl: string | null
  body: string
  comments: PostCommentEntry[]
  publishedAt: string
  id: string
  contentType: PostContentType
  mediaUrl: string | null
  title: string
}

interface PostWire {
  audio_url: string | null
  body: string
  comments: CommentWire[]
  id: string
  content_type: PostContentType
  media_url: string | null
  published_at: string
  title: string
}

interface ListWire {
  posts: PostWire[]
  next_cursor: string | null
  unread_post_ids: string[]
}

export const $posts = atom<PostEntry[]>([])
export const $postsHasUnread = atom(false)
export const $postsLoading = atom(false)
export const $postsHasMore = atom(false)
export const $postsLoadingMore = atom(false)
let cursor: string | null = null
let revision = 0
const deletedComments = new Set<string>()

const postsUnread = createUnreadMirror({
  scope: 'posts',
  unreadPath: '/api/companion/posts/unread',
  readPath: '/api/companion/posts/read',
  idField: 'post_ids',
  $hasUnread: $postsHasUnread
})

export const hydratePostsUnread = postsUnread.hydrate
export const markPostsRead = postsUnread.markRead

function comment(w: CommentWire): PostCommentEntry {
  return {
    content: w.content,
    createdAt: w.created_at,
    updatedAt: w.updated_at,
    id: w.id,
    postId: w.post_id,
    role: w.role,
    replyToCommentId: w.reply_to_comment_id,
    replyStatus: w.reply_status,
    replyError: w.reply_error
  }
}

function post(w: PostWire): PostEntry {
  return {
    audioUrl: w.audio_url,
    body: w.body,
    comments: w.comments.map(comment),
    publishedAt: w.published_at,
    id: w.id,
    contentType: w.content_type,
    mediaUrl: w.media_url,
    title: w.title
  }
}

function mergeComments(old: PostCommentEntry[], incoming: PostCommentEntry[]): PostCommentEntry[] {
  const map = new Map(old.map(c => [c.id, c]))

  for (const c of incoming) {
    const previous = map.get(c.id)

    if (!previous || Date.parse(c.updatedAt) >= Date.parse(previous.updatedAt)) {
      map.set(c.id, c)
    }
  }

  return [...map.values()]
    .filter(c => !deletedComments.has(c.id))
    .sort((a, b) => Date.parse(a.createdAt) - Date.parse(b.createdAt) || a.id.localeCompare(b.id))
}

function upsertPosts(wires: PostWire[]): void {
  if (wires.length === 0) {
    return
  }

  const merged = new Map($posts.get().map(value => [value.id, value]))

  for (const w of wires) {
    const value = post(w)
    value.comments = mergeComments(merged.get(value.id)?.comments ?? [], value.comments)
    merged.set(value.id, value)
  }

  $posts.set(
    [...merged.values()].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt) || b.id.localeCompare(a.id))
  )
}

function upsertComment(w: CommentWire): void {
  $posts.set(
    $posts.get().map(m => (m.id === w.post_id ? { ...m, comments: mergeComments(m.comments, [comment(w)]) } : m))
  )
}

async function fetchPage(more: boolean): Promise<string[] | null> {
  if (more && (!cursor || $postsLoadingMore.get() || $postsLoading.get())) {
    return null
  }

  const version = more ? revision : ++revision
  const epoch = currentClearEpoch()
  const loading = more ? $postsLoadingMore : $postsLoading

  if (!more) {
    $postsLoadingMore.set(false)
  }

  loading.set(true)

  try {
    const query = more && cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
    const result = await authedApi<ListWire>({ path: `/api/companion/posts${query}` })

    if (version !== revision || epoch !== currentClearEpoch()) {
      return null
    }

    if (
      !apiSucceeded(result, 'posts', 'load failed') ||
      !isRecord(result.value) ||
      !Array.isArray(result.value.posts) ||
      !result.value.posts.every(isPost) ||
      !Array.isArray(result.value.unread_post_ids) ||
      !result.value.unread_post_ids.every(id => typeof id === 'string') ||
      !(result.value.next_cursor === null || typeof result.value.next_cursor === 'string')
    ) {
      return null
    }

    upsertPosts(result.value.posts)

    cursor = result.value.next_cursor
    $postsHasMore.set(Boolean(cursor))

    return result.value.unread_post_ids
  } finally {
    if (version === revision) {
      loading.set(false)
    }
  }
}

export function hydratePosts(): Promise<string[] | null> {
  return fetchPage(false)
}

export async function loadMorePosts(): Promise<boolean> {
  return (await fetchPage(true)) !== null
}

async function postCommentResult(path: string, body?: { content: string }): Promise<boolean> {
  const epoch = currentClearEpoch()
  const result = await authedApi<CommentWire>({ method: 'POST', path, ...(body ? { body } : {}) })

  if (epoch !== currentClearEpoch() || !apiSucceeded(result, 'posts', 'comment failed') || !isComment(result.value)) {
    return false
  }

  upsertComment(result.value)

  return true
}

export function commentPost(id: string, content: string): Promise<boolean> {
  return postCommentResult(`/api/companion/posts/${id}/comments`, { content })
}

export function retryPostReply(id: string, commentId: string): Promise<boolean> {
  return postCommentResult(`/api/companion/posts/${id}/comments/${commentId}/reply/retry`)
}

function removeComment(postId: string, commentId: string): void {
  deletedComments.add(commentId)
  $posts.set(
    $posts.get().map(m => (m.id === postId ? { ...m, comments: m.comments.filter(c => c.id !== commentId) } : m))
  )
}

export async function deletePostComment(id: string, commentId: string): Promise<boolean> {
  const epoch = currentClearEpoch()
  const result = await authedApi<null>({ method: 'DELETE', path: `/api/companion/posts/${id}/comments/${commentId}` })

  if (epoch !== currentClearEpoch() || !apiSucceeded(result, 'posts', 'delete failed')) {
    return false
  }

  removeComment(id, commentId)

  return true
}

function isComment(value: unknown): value is CommentWire {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    typeof value.post_id === 'string' &&
    typeof value.content === 'string' &&
    typeof value.created_at === 'string' &&
    typeof value.updated_at === 'string' &&
    (value.role === 'user' || value.role === 'companion') &&
    typeof value.reply_status === 'string' &&
    ['none', 'pending', 'running', 'completed', 'failed'].includes(value.reply_status) &&
    (value.reply_to_comment_id === null || typeof value.reply_to_comment_id === 'string') &&
    (value.reply_error === null || typeof value.reply_error === 'string')
  )
}

function isPost(value: unknown): value is PostWire {
  return (
    isRecord(value) &&
    typeof value.id === 'string' &&
    typeof value.published_at === 'string' &&
    typeof value.title === 'string' &&
    typeof value.body === 'string' &&
    typeof value.content_type === 'string' &&
    ['text', 'image', 'video', 'audio'].includes(value.content_type) &&
    (value.media_url === null || typeof value.media_url === 'string') &&
    (value.audio_url === null || typeof value.audio_url === 'string') &&
    Array.isArray(value.comments) &&
    value.comments.every(isComment)
  )
}

export function onPostEvent(event: { type: string; payload?: unknown }): void {
  const w = event.payload

  if (event.type === 'companion.post.created' && isPost(w)) {
    upsertPosts([w])
    void hydratePostsUnread()
  }

  if (event.type === 'companion.posts.read') {
    void hydratePostsUnread()
  }

  if (event.type === 'companion.post.comment' && isRecord(w) && isComment(w.comment)) {
    upsertComment(w.comment)
  }

  if (
    event.type === 'companion.post.comment.deleted' &&
    isRecord(w) &&
    typeof w.post_id === 'string' &&
    typeof w.comment_id === 'string'
  ) {
    removeComment(w.post_id, w.comment_id)
  }
}

registerStorageClearHandler(() => {
  revision++
  cursor = null
  deletedComments.clear()
  $posts.set([])
  $postsLoading.set(false)
  $postsLoadingMore.set(false)
  $postsHasMore.set(false)
})
