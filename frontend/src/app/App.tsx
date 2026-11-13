import { ArrowUpRight, Compass, Library, ListFilter, MoveUpRight } from 'lucide-react'
import { Link, Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { DiscoveryPage } from '../features/discovery/DiscoveryPage'
import { RecommendationsPage } from '../features/recommendations/RecommendationsPage'
import { LibraryPage } from '../features/library/LibraryPage'
import { AccountControl } from '../features/auth/AccountControl'
import { LegacyLibraryRoute, LoginReturn, RequireAuth } from '../features/auth/AuthBoundary'

export function App() {
  const location = useLocation()
  return (
    <div className="app-shell">
      <LoginReturn />
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <Link to="/discover" className="brand" aria-label="GameRec home">
          <span className="brand-mark" aria-hidden="true">
            g<span>.</span>
          </span>
          <span>
            GameRec<span className="brand-caption">A different way to find games</span>
          </span>
        </Link>
        <AccountControl />
        <div className="nav-label">Your next session</div>
        <nav aria-label="Primary navigation" className="primary-nav">
          <NavLink to="/discover">
            <Compass size={18} aria-hidden="true" /> Discover
          </NavLink>
          <NavLink to="/recommendations">
            <ListFilter size={18} aria-hidden="true" /> Recommendations
          </NavLink>
          <NavLink to="/library">
            <Library size={18} aria-hidden="true" /> Library
          </NavLink>
        </nav>
        <div className="sidebar-note">
          <span className="small-label">Built around your taste</span>
          <p>Start with what you want to play. Find something worth your time.</p>
          <a href="https://store.steampowered.com/" target="_blank" rel="noreferrer">
            Browse Steam <ArrowUpRight size={14} aria-hidden="true" />
          </a>
        </div>
      </aside>
      <main id="main" className="main-content" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Navigate to="/discover" replace />} />
          <Route path="/discover" element={<DiscoveryPage />} />
          <Route element={<RequireAuth key={location.pathname} />}>
            <Route path="/recommendations" element={<RecommendationsPage />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/library/:steamid64" element={<LegacyLibraryRoute />} />
          </Route>
          <Route
            path="*"
            element={
              <section className="not-found">
                <span className="small-label">404</span>
                <h1>Page not found</h1>
                <Link to="/discover">
                  Back to Discover <MoveUpRight size={16} aria-hidden="true" />
                </Link>
              </section>
            }
          />
        </Routes>
      </main>
    </div>
  )
}
