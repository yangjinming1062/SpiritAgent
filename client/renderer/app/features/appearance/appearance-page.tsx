import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { useAssetLibrarySearch } from '@/app/components/asset-library-header'
import { $outfits, hydrateAvatarSeeds, hydrateWardrobe } from '@/modules/character'
import { $auth } from '@/shared/store/auth'

import { $appearanceUi, updateAppearanceUi } from './appearance-ui-state'
import { OutfitDetail } from './outfit-detail'
import { OutfitSection } from './outfit-section'

export function AppearancePage(): React.JSX.Element {
  const authKind = useStore($auth).kind
  const outfits = useStore($outfits)
  const { selectedOutfitId } = useStore($appearanceUi)
  const [loaded, setLoaded] = useState(false)
  const librarySearch = useAssetLibrarySearch()
  const setSelectedOutfitId = (id: string | null): void => updateAppearanceUi({ selectedOutfitId: id })

  // 冷启动须等待鉴权，否则水合会跳过读取。
  useEffect(() => {
    if (authKind === 'authenticated') {
      let current = true
      void hydrateWardrobe().finally(() => {
        if (current) {
          setLoaded(true)
        }
      })
      void hydrateAvatarSeeds()

      return () => {
        current = false
      }
    }

    setLoaded(false)
  }, [authKind])

  useEffect(() => {
    if (loaded && selectedOutfitId !== null && !outfits.some(outfit => outfit.id === selectedOutfitId)) {
      setSelectedOutfitId(null)
    }
  }, [loaded, outfits, selectedOutfitId])

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {selectedOutfitId === null ? (
        <OutfitSection onSelectOutfit={setSelectedOutfitId} search={librarySearch} />
      ) : (
        <OutfitDetail onBack={() => setSelectedOutfitId(null)} outfitId={selectedOutfitId} />
      )}
    </div>
  )
}
