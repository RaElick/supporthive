import { Fragment, useCallback, useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, fmtDateTime, fmtDuration, subscribe, tokens } from '../api'
import type { Operator } from '../types'
import { CATEGORY_LABELS } from '../types'

const C1 = '#3b6fd8'
const C2 = '#19a974'
const GRID = 'var(--line)'

export default function Admin() {
  return (
    <div className="admin">
      <nav className="subnav">
        <NavLink to="/admin/dashboard">Дашборд</NavLink>
        <NavLink to="/admin/routing">Маршрутизация</NavLink>
        <NavLink to="/admin/agents">Трассировка агентов</NavLink>
        <NavLink to="/admin/operators">Операторы</NavLink>
        <NavLink to="/admin/kb">База знаний</NavLink>
      </nav>
      <Routes>
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="routing" element={<Routing />} />
        <Route path="agents" element={<AgentRuns />} />
        <Route path="operators" element={<Operators />} />
        <Route path="kb" element={<Kb />} />
        <Route path="*" element={<Navigate to="/admin/dashboard" replace />} />
      </Routes>
    </div>
  )
}

function Kpi({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="card kpi">
      <div className="small muted">{label}</div>
      <div className="kpi-value">{value ?? '—'}</div>
      {hint && <div className="small muted">{hint}</div>}
    </div>
  )
}

function Dashboard() {
  const [days, setDays] = useState(14)
  const [d, setD] = useState<any>(null)
  const load = useCallback(() => api(`/api/admin/dashboard?days=${days}`).then(setD), [days])
  useEffect(() => {
    load()
  }, [load])
  useEffect(() => {
    let t: number | undefined
    return subscribe('/ws/operators', tokens.operator!, () => {
      clearTimeout(t)
      t = window.setTimeout(load, 1000)
    })
  }, [load])
  if (!d) return <p className="pad">Загрузка…</p>
  const o = d.overview
  return (
    <div className="dash">
      <div className="dash-head">
        <h2>Работа поддержки</h2>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {[1, 7, 14, 30].map((n) => (
            <option key={n} value={n}>
              за {n} дн.
            </option>
          ))}
        </select>
      </div>
      <div className="kpis">
        <Kpi label="Обращений" value={o.total} hint={`открыто ${o.open}, в очереди ${o.queued}`} />
        <Kpi label="Первый ответ (сред.)" value={fmtDuration(o.avg_first_response_s)} />
        <Kpi label="Время решения (сред.)" value={fmtDuration(o.avg_resolution_s)} />
        <Kpi label="Оценка клиентов" value={o.avg_rating?.toFixed(2)} hint={o.csat_pct != null ? `CSAT ${o.csat_pct}%` : ''} />
        <Kpi label="Качество (агент)" value={o.avg_quality_score != null ? `${o.avg_quality_score}/10` : null} />
        <Kpi label="Операторов на линии" value={o.online_operators} />
      </div>

      <div className="grid2">
        <div className="card">
          <h4>Поток обращений</h4>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={d.daily} margin={{ left: -20, right: 8 }}>
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="date" tickFormatter={(v) => v.slice(5)} fontSize={12} />
              <YAxis allowDecimals={false} fontSize={12} />
              <Tooltip />
              <Legend />
              <Line dataKey="created" name="Создано" stroke={C1} strokeWidth={2} dot={false} />
              <Line dataKey="closed" name="Закрыто" stroke={C2} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <h4>Темы обращений</h4>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={d.categories} layout="vertical" margin={{ left: 40, right: 16 }}>
              <CartesianGrid stroke={GRID} horizontal={false} />
              <XAxis type="number" allowDecimals={false} fontSize={12} />
              <YAxis type="category" dataKey="label" width={110} fontSize={12} />
              <Tooltip />
              <Bar dataKey="count" name="Обращений" fill={C1} radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <h4>Операторы</h4>
        <table>
          <thead>
            <tr>
              <th>Оператор</th>
              <th>Навыки</th>
              <th className="num">Закрыто</th>
              <th className="num">В работе</th>
              <th className="num">Первый ответ</th>
              <th className="num">Решение</th>
              <th className="num">Оценка</th>
              <th className="num">Качество</th>
            </tr>
          </thead>
          <tbody>
            {d.operators.map((r: any) => (
              <tr key={r.id}>
                <td>
                  <span className={`dot ${r.online ? 'on' : ''}`} /> {r.name}
                </td>
                <td className="small">{r.skills.map((s: string) => CATEGORY_LABELS[s] ?? s).join(', ')}</td>
                <td className="num">{r.handled}</td>
                <td className="num">{r.open}</td>
                <td className="num">{fmtDuration(r.avg_first_response_s)}</td>
                <td className="num">{fmtDuration(r.avg_resolution_s)}</td>
                <td className={`num ${r.avg_rating != null && r.avg_rating < 3.5 ? 'bad' : ''}`}>{r.avg_rating ?? '—'}</td>
                <td className="num">{r.avg_quality_score ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h4>Аномалии ({d.anomalies.length})</h4>
        {d.anomalies.length === 0 && <p className="muted">Нет</p>}
        <ul className="anomalies">
          {d.anomalies.map((a: any, i: number) => (
            <li key={i}>
              <span className={`tag ${a.kind === 'quality' ? 'warn' : ''}`}>{a.kind === 'quality' ? 'качество' : 'долго'}</span>
              #{a.ticket_id} · {a.operator_name ?? '—'} · {a.text}
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

function Routing() {
  const [settings, setSettings] = useState<any>(null)
  const [log, setLog] = useState<any[]>([])
  const [stats, setStats] = useState<any[]>([])
  const [msg, setMsg] = useState('')
  const load = useCallback(async () => {
    const [s, l, d] = await Promise.all([api('/api/admin/settings'), api('/api/admin/routing-log?limit=40'), api('/api/admin/dashboard?days=30')])
    setSettings(s)
    setLog(l)
    setStats(d.routing)
  }, [])
  useEffect(() => {
    load()
  }, [load])
  if (!settings) return <p className="pad">Загрузка…</p>
  const save = async (patch: object) => {
    setSettings(await api('/api/admin/settings', { method: 'PUT', body: patch }))
    const r = await api('/api/admin/dispatch', { body: {} })
    setMsg(`Сохранено. Распределено из очереди: ${r.assigned.length}`)
    load()
  }
  const desc: Record<string, string> = {
    queue: 'Общая очередь: свободный оператор с наименьшей загрузкой',
    skills: 'Сопоставление навыков с темой + история оценок − загрузка',
    llm: 'LLM выбирает из кандидатов (с проверкой и откатом на skills)',
  }
  return (
    <div className="dash">
      <div className="grid2">
        <div className="card">
          <h4>Стратегия маршрутизации</h4>
          {settings.strategies.map((s: string) => (
            <label key={s} className="radio">
              <input type="radio" checked={settings.routing_strategy === s} onChange={() => save({ routing_strategy: s })} />
              <b>{s}</b> — {desc[s]}
            </label>
          ))}
          <h4>Размещение контекста подсказчика</h4>
          {settings.placements.map((p: string) => (
            <label key={p} className="radio">
              <input type="radio" checked={settings.copilot_placement === p} onChange={() => save({ copilot_placement: p })} />
              <b>{p}</b> — {p === 'system_first' ? 'всё в system, задача в конце' : 'статьи и диалог прямо перед задачей'}
            </label>
          ))}
          <p className="small muted">LLM: {settings.llm_enabled ? 'подключён' : 'не настроен — агенты работают на эвристиках'}</p>
          {msg && <p className="small ok">{msg}</p>}
        </div>
        <div className="card">
          <h4>Сравнение стратегий (30 дней)</h4>
          <table>
            <thead>
              <tr>
                <th>Стратегия</th>
                <th className="num">Решений</th>
                <th className="num">По профилю</th>
                <th className="num">Оценка</th>
                <th className="num">Время решения</th>
              </tr>
            </thead>
            <tbody>
              {stats.map((r) => (
                <tr key={r.strategy}>
                  <td>{r.strategy}</td>
                  <td className="num">{r.decisions}</td>
                  <td className="num">{r.skill_match_pct ?? '—'}%</td>
                  <td className="num">{r.avg_rating ?? '—'}</td>
                  <td className="num">{fmtDuration(r.avg_resolution_s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted">Офлайн-сравнение на одинаковой нагрузке: <code>python -m app.simulate</code></p>
        </div>
      </div>
      <div className="card">
        <h4>Журнал решений маршрутизатора</h4>
        <table>
          <thead>
            <tr>
              <th>Время</th>
              <th>Обращение</th>
              <th>Стратегия</th>
              <th>Оператор</th>
              <th>Обоснование</th>
            </tr>
          </thead>
          <tbody>
            {log.map((r) => (
              <tr key={r.id}>
                <td className="small">{fmtDateTime(r.created_at)}</td>
                <td>#{r.ticket_id}</td>
                <td>
                  <span className="tag">{r.strategy}</span> <span className="tag">{r.mode}</span>
                </td>
                <td>{r.operator_name}</td>
                <td className="small">{r.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function AgentRuns() {
  const [agent, setAgent] = useState('')
  const [runs, setRuns] = useState<any[]>([])
  const [open, setOpen] = useState<number | null>(null)
  useEffect(() => {
    api(`/api/admin/agent-runs?limit=100${agent ? `&agent=${agent}` : ''}`).then(setRuns)
  }, [agent])
  return (
    <div className="dash">
      <div className="dash-head">
        <h2>Трассировка агентов</h2>
        <select value={agent} onChange={(e) => setAgent(e.target.value)}>
          <option value="">все агенты</option>
          {['triage', 'router', 'copilot', 'quality'].map((a) => (
            <option key={a}>{a}</option>
          ))}
        </select>
      </div>
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Время</th>
              <th>Агент</th>
              <th>Обращение</th>
              <th>Режим</th>
              <th className="num">мс</th>
              <th>Контекст</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <Fragment key={r.id}>
                <tr className={r.error ? 'bad' : ''}>
                  <td className="small">{fmtDateTime(r.created_at)}</td>
                  <td>{r.agent}</td>
                  <td>{r.ticket_id ? `#${r.ticket_id}` : '—'}</td>
                  <td>
                    <span className="tag">{r.mode}</span>
                  </td>
                  <td className="num">{r.duration_ms}</td>
                  <td className="small">
                    {r.input?.context?.used_tokens != null
                      ? `${r.input.context.used_tokens}/${r.input.context.budget_tokens} ток., вытеснено ${r.input.context.evicted.length}`
                      : '—'}
                  </td>
                  <td>
                    <button className="ghost small" onClick={() => setOpen(open === r.id ? null : r.id)}>
                      {open === r.id ? 'скрыть' : 'детали'}
                    </button>
                  </td>
                </tr>
                {open === r.id && (
                  <tr key={`${r.id}-d`}>
                    <td colSpan={7}>
                      <pre>{JSON.stringify({ input: r.input, output: r.output, error: r.error }, null, 2)}</pre>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const SKILLS = Object.keys(CATEGORY_LABELS)

function Operators() {
  const [ops, setOps] = useState<Operator[]>([])
  const [form, setForm] = useState({ login: '', name: '', password: '', skills: [] as string[], max_concurrent: 3 })
  const [err, setErr] = useState('')
  const load = () => api<Operator[]>('/api/admin/operators').then(setOps)
  useEffect(() => {
    load()
  }, [])
  const update = async (o: Operator, patch: Partial<Operator>) => {
    const next = { ...o, ...patch }
    await api(`/api/admin/operators/${o.id}`, {
      method: 'PUT',
      body: { login: next.login, name: next.name, skills: next.skills, max_concurrent: next.max_concurrent, is_admin: next.is_admin },
    })
    load()
  }
  const create = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    try {
      await api('/api/admin/operators', { body: form })
      setForm({ login: '', name: '', password: '', skills: [], max_concurrent: 3 })
      load()
    } catch (e) {
      setErr((e as Error).message)
    }
  }
  return (
    <div className="dash">
      <div className="card">
        <h4>Операторы и компетенции</h4>
        <table>
          <thead>
            <tr>
              <th>Оператор</th>
              <th>Навыки (используются маршрутизатором)</th>
              <th className="num">Макс. диалогов</th>
            </tr>
          </thead>
          <tbody>
            {ops
              .filter((o) => !o.is_admin)
              .map((o) => (
                <tr key={o.id}>
                  <td>
                    <span className={`dot ${o.online ? 'on' : ''}`} /> {o.name} <span className="muted small">({o.login})</span>
                  </td>
                  <td>
                    {SKILLS.map((s) => (
                      <label key={s} className="chip">
                        <input
                          type="checkbox"
                          checked={o.skills.includes(s)}
                          onChange={(e) =>
                            update(o, { skills: e.target.checked ? [...o.skills, s] : o.skills.filter((x) => x !== s) })
                          }
                        />
                        {CATEGORY_LABELS[s]}
                      </label>
                    ))}
                  </td>
                  <td className="num">
                    <input
                      type="number"
                      min={1}
                      max={20}
                      value={o.max_concurrent}
                      onChange={(e) => update(o, { max_concurrent: Number(e.target.value) })}
                      className="narrow"
                    />
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      <form className="card form-row" onSubmit={create}>
        <h4>Новый оператор</h4>
        <input placeholder="Логин" value={form.login} onChange={(e) => setForm({ ...form, login: e.target.value })} />
        <input placeholder="Имя" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input placeholder="Пароль" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <div>
          {SKILLS.map((s) => (
            <label key={s} className="chip">
              <input
                type="checkbox"
                checked={form.skills.includes(s)}
                onChange={(e) =>
                  setForm({ ...form, skills: e.target.checked ? [...form.skills, s] : form.skills.filter((x) => x !== s) })
                }
              />
              {CATEGORY_LABELS[s]}
            </label>
          ))}
        </div>
        <button type="submit">Добавить</button>
        {err && <span className="error">{err}</span>}
      </form>
    </div>
  )
}

function Kb() {
  const [arts, setArts] = useState<any[]>([])
  const [form, setForm] = useState({ category: 'general', title: '', body: '' })
  const load = () => api('/api/admin/kb').then(setArts)
  useEffect(() => {
    load()
  }, [])
  const create = async (e: React.FormEvent) => {
    e.preventDefault()
    await api('/api/admin/kb', { body: form })
    setForm({ category: 'general', title: '', body: '' })
    load()
  }
  return (
    <div className="dash">
      <form className="card form-col" onSubmit={create}>
        <h4>Новая статья</h4>
        <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
          {SKILLS.map((s) => (
            <option key={s} value={s}>
              {CATEGORY_LABELS[s]}
            </option>
          ))}
        </select>
        <input placeholder="Заголовок" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        <textarea placeholder="Текст" rows={4} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
        <button type="submit" disabled={!form.title || !form.body}>
          Добавить
        </button>
      </form>
      <div className="kb-grid">
        {arts.map((a) => (
          <div className="card" key={a.id}>
            <div className="row-top">
              <span className="tag">{CATEGORY_LABELS[a.category]}</span>
              <button className="ghost small" onClick={() => api(`/api/admin/kb/${a.id}`, { method: 'DELETE' }).then(load)}>
                удалить
              </button>
            </div>
            <b>{a.title}</b>
            <p className="small">{a.body}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
