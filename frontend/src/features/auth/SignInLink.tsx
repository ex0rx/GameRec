import { LogIn } from 'lucide-react'
import { useLocation } from 'react-router-dom'
import { steamLoginUrl } from '../../api/client'
import { rememberLoginDestination } from './authNavigation'

export function SignInLink({ className = 'button secondary' }: { className?: string }) {
  const location = useLocation()
  return (
    <a
      className={className}
      href={steamLoginUrl}
      onClick={(event) => {
        if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
          rememberLoginDestination(location.pathname)
        }
      }}
    >
      <LogIn size={14} aria-hidden="true" /> Sign in with Steam
    </a>
  )
}
