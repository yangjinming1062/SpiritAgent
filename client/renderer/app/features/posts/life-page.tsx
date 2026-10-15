import { useStore } from '@nanostores/react'
import type React from 'react'
import type { RefObject } from 'react'

import { DiaryPage } from '@/app/features/diary/diary-page'
import { $postsTab, setPostsTab } from '@/app/navigation/app-navigation'
import { $diaryHasUnread } from '@/modules/diary'
import { MediaViewerOverlay } from '@/modules/media'
import { $postsHasUnread } from '@/modules/posts'
import { CapsuleTabs } from '@/shared/panel'
import { $locale } from '@/shared/store/locale'

import { PostsPage } from './posts-page'

export function LifePage({
  foreground,
  mediaViewId,
  containerRef
}: {
  foreground: boolean
  mediaViewId: string
  containerRef?: RefObject<HTMLElement | null>
}): React.JSX.Element {
  const tab = useStore($postsTab)
  const postsUnread = useStore($postsHasUnread)
  const diaryUnread = useStore($diaryHasUnread)
  const english = useStore($locale) === 'en'

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-line-hairline px-5 py-3">
        <CapsuleTabs
          ariaLabel={english ? 'Life updates' : '伙伴的日常'}
          onChange={setPostsTab}
          options={[
            { value: 'posts', label: `${english ? 'Updates' : '动态'}${postsUnread ? ' ·' : ''}` },
            { value: 'diary', label: `${english ? 'Diary' : '日记'}${diaryUnread ? ' ·' : ''}` }
          ]}
          value={tab}
        />
      </div>
      <div className="min-h-0 flex-1">
        {tab === 'posts' ? (
          <>
            <PostsPage foreground={foreground} mediaViewId={mediaViewId} />
            <MediaViewerOverlay containerRef={containerRef} viewId={mediaViewId} windowId={1} />
          </>
        ) : (
          <DiaryPage foreground={foreground} />
        )}
      </div>
    </div>
  )
}
