import { LogOut, UserRound } from 'lucide-react'
import { useIsMutating } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useCurrentUser } from './useCurrentUser'
import { useLogout } from './useLogout'
import { SignInLink } from './SignInLink'
import './auth.css'

export function AccountControl() {
  const currentUser = useCurrentUser()
  const logout = useLogout()
  const navigate = useNavigate()
  const signingOut = useIsMutating({ mutationKey: ['auth-logout'] }) > 0
  const checking = currentUser.isPending || currentUser.isFetching

  return (
    <div className="account-control" aria-label="Steam account">
      {signingOut ? (
        <p className="account-message" role="status">Signing out…</p>
      ) : checking ? (
        <p className="account-message" role="status">Checking session…</p>
      ) : currentUser.isError ? (
        <>
          <p className="account-message">Steam sign-in is unavailable.</p>
          <button
            className="account-action text-button"
            onClick={() => { void currentUser.refetch() }}
          >
            Retry sign-in status
          </button>
        </>
      ) : currentUser.data ? (
        <>
          <span className="account-status">
            <UserRound size={14} aria-hidden="true" /> Signed in with Steam
          </span>
          <button
            className="account-action text-button"
            onClick={() => logout.mutate(undefined, {
              onSuccess: () => { void navigate('/discover', { replace: true }) },
            })}
            disabled={signingOut}
          >
            <LogOut size={14} aria-hidden="true" /> Sign out
          </button>
        </>
      ) : (
        <SignInLink className="button secondary account-sign-in" />
      )}
      {logout.isError && (
        <p className="account-error" role="alert">Could not confirm sign-out. Try again.</p>
      )}
    </div>
  )
}
