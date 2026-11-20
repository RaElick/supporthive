import { useEffect, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { api, tokens } from './api'
import type { Operator } from './types'
import Admin from './pages/Admin'
import Home from './pages/Home'
import Login from './pages/Login'
import OperatorDesk from './pages/OperatorDesk'
import Widget from './pages/Widget'

export default function App() {
  const [me, setMe] = useState<Operator | null>(null)
  const [checked, setChecked] = useState(false)
  const nav = useNavigate()

  useEffect(() => {
    if (!tokens.operator) {
      setChecked(true)
      return
    }
    api<Operator>('/api/me')
      .then(setMe)
      .catch(() => (tokens.operator = null))
      .finally(() => setChecked(true))
  }, [])

  const logout = async () => {
    if (me && !me.is_admin) await api('/api/me/status', { body: { online: false } }).catch(() => {})
    tokens.operator = null
    setMe(null)
    nav('/login')
  }

  const guard = (el: React.ReactNode, admin = false) => {
    if (!checked) return null
    if (!me) return <Navigate to="/login" replace />
    if (admin && !me.is_admin) return <Navigate to="/desk" replace />
    return el
  }

  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/shop" element={<Widget />} />
      <Route path="/login" element={<Login onLogin={setMe} />} />
      <Route
        path="/*"
        element={guard(
          <div className="app">
            <header className="topbar">
              <Link to="/" className="brand">
                SupportHive
              </Link>
              <nav>
                <NavLink to="/desk">Обращения</NavLink>
                {me?.is_admin && <NavLink to="/admin">Аналитика</NavLink>}
              </nav>
              <div className="spacer" />
              <span className="muted">{me?.name}</span>
              <button className="ghost" onClick={logout}>
                Выйти
              </button>
            </header>
            <Routes>
              <Route path="desk" element={me && <OperatorDesk me={me} setMe={setMe} />} />
              <Route path="admin/*" element={guard(<Admin />, true)} />
              <Route path="*" element={<Navigate to={me?.is_admin ? '/admin' : '/desk'} replace />} />
            </Routes>
          </div>,
        )}
      />
    </Routes>
  )
}
