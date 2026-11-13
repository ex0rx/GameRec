import { useEffect } from 'react'
import { useIsMutating } from '@tanstack/react-query'
import {
  Link,
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useOutletContext,
  useParams,
  useSearchParams,
} from 'react-router-dom'
import type { CurrentUser } from '../../api/types'
import { ErrorState } from '../../components/States'
import { myLibraryPath, readLibraryPage, validLibrarySteamId } from '../library/libraryState'
import { useCurrentUser } from './useCurrentUser'
import { SignInLink } from './SignInLink'
import { consumeLoginDestination } from './authNavigation'

export function RequireAuth() {
  const currentUser = useCurrentUser()
  const signingOut = useIsMutating({ mutationKey: ['auth-logout'] }) > 0
  if (signingOut || currentUser.isPending || currentUser.isFetching) {
    return (
      <section className="feedback-state auth-state" role="status" aria-label="Checking your Steam session">
        <p>{signingOut ? 'Signing out…' : 'Checking your Steam session…'}</p>
      </section>
    )
  }
  if (currentUser.isError) {
    return (
      <section className="auth-state">
        <ErrorState
          title="Steam sign-in is unavailable"
          message="Your session could not be checked. Public discovery is still available."
          onRetry={() => { void currentUser.refetch() }}
        />
      </section>
    )
  }
  if (!currentUser.data) {
    return (
      <section className="feedback-state auth-state" aria-labelledby="sign-in-required-heading">
        <h1 id="sign-in-required-heading">Sign in to continue</h1>
        <p>Sign in with Steam to access your Library and Recommendations.</p>
        <SignInLink />
      </section>
    )
  }
  return <Outlet key={currentUser.data.steamid64} context={currentUser.data} />
}

export function LegacyLibraryRoute() {
  const user = useOutletContext<CurrentUser>()
  const { steamid64 } = useParams()
  const [params] = useSearchParams()
  if (validLibrarySteamId(steamid64) && steamid64 === user.steamid64) {
    return <Navigate to={myLibraryPath(readLibraryPage(params))} replace />
  }
  return (
    <section className="feedback-state auth-state" aria-labelledby="legacy-library-heading">
      <h1 id="legacy-library-heading">Library unavailable</h1>
      <p>This link cannot open a library for your signed-in account.</p>
      <Link className="button secondary" to="/library">View your library</Link>
    </section>
  )
}

export function LoginReturn() {
  const currentUser = useCurrentUser()
  const location = useLocation()
  const navigate = useNavigate()
  useEffect(() => {
    if (
      location.pathname !== '/discover' ||
      !currentUser.isSuccess ||
      currentUser.isFetching ||
      !currentUser.data
    ) return
    const destination = consumeLoginDestination()
    if (destination && destination !== '/discover') {
      void navigate(destination, { replace: true })
    }
  }, [currentUser.data, currentUser.isFetching, currentUser.isSuccess, location.pathname, navigate])
  return null
}
