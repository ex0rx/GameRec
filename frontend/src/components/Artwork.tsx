import { useState } from 'react'
import { ImageOff } from 'lucide-react'

export function Artwork({ appId, name }: { appId: number; name: string }) {
  const [failed, setFailed] = useState(false)
  return (
    <div className="artwork">
      {failed ? (
        <div className="artwork-fallback" role="img" aria-label={`${name}: artwork unavailable`}>
          <ImageOff size={22} aria-hidden="true" />
          <span>Artwork unavailable</span>
        </div>
      ) : (
        <img
          src={`https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/${appId}/header.jpg`}
          alt={`${name} artwork`}
          width={460}
          height={215}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
        />
      )}
    </div>
  )
}
