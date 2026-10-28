import { useEffect, useState } from 'react'
import { fetchMaterialBlobUrl } from '@/api/production/production.api'

/** `pack:slug/name` 对应的素材原图相对路径，和后端只读图片通道约定一致。 */
export function packShotPath(ref: string): string | null {
  if (!ref.startsWith('pack:'))
    return null
  const [slug, ...rest] = ref.slice(5).split('/')
  const name = rest.join('/')
  if (!slug || !name)
    return null
  return `packs/${slug}/shots/${name}.png`
}

/** 把 `pack:` 引用批量换成可直接 <img src> 的 blob 地址；取不到的引用不出缩略图。 */
export function useMaterialThumbs(refs: string[]) {
  const [thumbs, setThumbs] = useState<Record<string, string>>({})

  useEffect(() => {
    let alive = true
    const created: string[] = []
    refs.forEach((ref) => {
      const rel = packShotPath(ref)
      if (!rel)
        return
      fetchMaterialBlobUrl(rel)
        .then((url) => {
          if (!url || !alive)
            return
          created.push(url)
          setThumbs(current => ({ ...current, [ref]: url }))
        })
        .catch(() => {})
    })
    return () => {
      alive = false
      created.forEach(url => URL.revokeObjectURL(url))
    }
  }, [refs])

  return thumbs
}
