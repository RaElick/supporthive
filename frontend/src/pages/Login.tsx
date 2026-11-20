import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, tokens } from '../api'
import type { Operator } from '../types'

export default function Login({ onLogin }: { onLogin: (o: Operator) => void }) {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const nav = useNavigate()

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      const r = await api<{ token: string; operator: Operator }>('/api/auth/login', {
        body: { login, password },
        token: null,
      })
      tokens.operator = r.token
      onLogin(r.operator)
      nav(r.operator.is_admin ? '/admin' : '/desk')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <div className="center">
      <form className="card login" onSubmit={submit}>
        <h2>Вход в SupportHive</h2>
        <label>
          Логин
          <input value={login} onChange={(e) => setLogin(e.target.value)} autoFocus />
        </label>
        <label>
          Пароль
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="error">{error}</div>}
        <button type="submit" disabled={!login || !password}>
          Войти
        </button>
        <p className="muted small">Демо: anna / demo1234, admin / admin1234</p>
      </form>
    </div>
  )
}
