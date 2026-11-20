import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fmtDuration, fmtTime, subscribe, tokens } from '../api'
import type { Operator, Ticket } from '../types'
import { CATEGORY_LABELS, FLAG_LABELS, PRIORITY_LABELS, STATUS_LABELS } from '../types'

type Scope = 'mine' | 'queue' | 'closed'

export default function OperatorDesk({ me, setMe }: { me: Operator; setMe: (o: Operator) => void }) {
  const [scope, setScope] = useState<Scope>(me.is_admin ? 'queue' : 'mine')
  const [list, setList] = useState<Ticket[]>([])
  const [counts, setCounts] = useState<Record<Scope, number>>({ mine: 0, queue: 0, closed: 0 })
  const [selId, setSelId] = useState<number | null>(null)
  const [sel, setSel] = useState<Ticket | null>(null)
  const [reply, setReply] = useState('')
  const [suggestion, setSuggestion] = useState<{ reply: string; articles: { id: number; title: string }[]; mode: string } | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const [operators, setOperators] = useState<Operator[]>([])
  const [error, setError] = useState('')
  const bottom = useRef<HTMLDivElement>(null)
  const selRef = useRef<number | null>(null)
  selRef.current = selId

  const loadLists = useCallback(async () => {
    const [mine, queue, closed] = await Promise.all(
      (['mine', 'queue', 'closed'] as Scope[]).map((s) => api<Ticket[]>(`/api/tickets?scope=${s}&limit=100`)),
    )
    const all = { mine, queue, closed }
    setCounts({ mine: mine.length, queue: queue.length, closed: closed.length })
    setList(all[scope])
  }, [scope])

  const loadSel = useCallback(async (id: number | null) => {
    if (id == null) return setSel(null)
    setSel(await api<Ticket>(`/api/tickets/${id}`))
  }, [])

  useEffect(() => {
    loadLists()
  }, [loadLists])

  useEffect(() => {
    setSuggestion(null)
    setReply('')
    loadSel(selId)
  }, [selId, loadSel])

  useEffect(() => {
    api<Operator[]>('/api/operators').then(setOperators)
  }, [])

  useEffect(() => {
    let timer: number | undefined
    const refresh = () => {
      clearTimeout(timer)
      timer = window.setTimeout(() => {
        loadLists()
        if (selRef.current != null) loadSel(selRef.current)
      }, 150)
    }
    return subscribe('/ws/operators', tokens.operator!, (e) => {
      if (e.type === 'operator_status') {
        setOperators((ops) => ops.map((o) => (o.id === e.data.id ? { ...o, ...e.data } : o)))
        return
      }
      refresh()
    })
  }, [loadLists, loadSel])

  useEffect(() => {
    bottom.current?.scrollIntoView()
  }, [sel?.messages?.length])

  const toggleOnline = async () => {
    const r = await api<Operator>('/api/me/status', { body: { online: !me.online } })
    setMe({ ...me, ...r })
  }

  const act = async (fn: () => Promise<unknown>) => {
    setError('')
    try {
      await fn()
      await Promise.all([loadLists(), loadSel(selId)])
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const send = (e: React.FormEvent) => {
    e.preventDefault()
    if (!reply.trim() || !sel) return
    act(async () => {
      await api(`/api/tickets/${sel.id}/messages`, { body: { text: reply } })
      setReply('')
      setSuggestion(null)
    })
  }

  const suggest = async () => {
    if (!sel) return
    setSuggesting(true)
    try {
      setSuggestion(await api(`/api/tickets/${sel.id}/suggest`, { body: {} }))
    } finally {
      setSuggesting(false)
    }
  }

  const canWrite = sel && sel.status === 'assigned' && (sel.operator_id === me.id || me.is_admin)

  return (
    <div className="desk">
      <aside className="desk-list">
        {!me.is_admin && (
          <div className="presence">
            <span className={`dot ${me.online ? 'on' : ''}`} />
            {me.online ? 'На линии' : 'Не на линии'}
            <button className={me.online ? 'ghost' : ''} onClick={toggleOnline}>
              {me.online ? 'Уйти' : 'Выйти на линию'}
            </button>
          </div>
        )}
        <div className="tabs">
          {(['mine', 'queue', 'closed'] as Scope[]).map((s) => (
            <button key={s} className={scope === s ? 'active' : ''} onClick={() => setScope(s)}>
              {{ mine: 'Мои', queue: 'Очередь', closed: 'Закрытые' }[s]} <span className="badge">{counts[s]}</span>
            </button>
          ))}
        </div>
        <div className="list">
          {list.length === 0 && <p className="muted pad">Пусто</p>}
          {list.map((t) => (
            <button key={t.id} className={`ticket-row ${selId === t.id ? 'sel' : ''}`} onClick={() => setSelId(t.id)}>
              <div className="row-top">
                <b>{t.customer.name}</b>
                <span className={`prio ${t.priority}`}>{PRIORITY_LABELS[t.priority]}</span>
              </div>
              <div className="small muted">
                #{t.id} · {t.channel === 'telegram' ? 'Telegram' : 'сайт'} ·{' '}
                {t.category ? CATEGORY_LABELS[t.category] : 'классифицируется…'}
              </div>
              <div className="small ellipsis">{t.summary ?? t.last_message?.text}</div>
            </button>
          ))}
        </div>
      </aside>

      <section className="desk-chat">
        {!sel ? (
          <div className="center muted">Выберите обращение</div>
        ) : (
          <>
            <div className="chat-head">
              <div>
                <b>
                  #{sel.id} · {sel.customer.name}
                </b>
                <div className="small muted">
                  {STATUS_LABELS[sel.status]}
                  {sel.operator_name && ` · ${sel.operator_name}`}
                </div>
              </div>
              <div className="spacer" />
              {(sel.status === 'queued' || sel.status === 'new') && (
                <button onClick={() => act(() => api(`/api/tickets/${sel.id}/take`, { body: {} }))}>Взять</button>
              )}
              {canWrite && (
                <>
                  <select
                    value=""
                    onChange={(e) =>
                      e.target.value &&
                      act(() => api(`/api/tickets/${sel.id}/transfer`, { body: { operator_id: Number(e.target.value) } }))
                    }
                  >
                    <option value="">Передать…</option>
                    {operators
                      .filter((o) => !o.is_admin && o.id !== sel.operator_id)
                      .map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.name} {o.online ? '' : '(не на линии)'}
                        </option>
                      ))}
                  </select>
                  <button className="ghost" onClick={() => act(() => api(`/api/tickets/${sel.id}/close`, { body: { resolved: false } }))}>
                    Закрыть без решения
                  </button>
                  <button onClick={() => act(() => api(`/api/tickets/${sel.id}/close`, { body: { resolved: true } }))}>
                    Решено
                  </button>
                </>
              )}
            </div>
            <div className="chat-body">
              {sel.messages?.map((m) => (
                <div key={m.id} className={`bubble ${m.sender === 'operator' ? 'mine' : m.sender}`}>
                  {m.text}
                  <span className="time">{fmtTime(m.created_at)}</span>
                </div>
              ))}
              <div ref={bottom} />
            </div>
            {error && <div className="error pad">{error}</div>}
            {canWrite && (
              <form className="chat-input" onSubmit={send}>
                <textarea
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  placeholder="Ответ клиенту… (Ctrl+Enter — отправить)"
                  onKeyDown={(e) => e.key === 'Enter' && e.ctrlKey && send(e)}
                  rows={3}
                />
                <div className="chat-actions">
                  <button type="button" className="ghost" onClick={suggest} disabled={suggesting}>
                    {suggesting ? 'Агент думает…' : '✨ Подсказка агента'}
                  </button>
                  <button type="submit" disabled={!reply.trim()}>
                    Отправить
                  </button>
                </div>
              </form>
            )}
          </>
        )}
      </section>

      <aside className="desk-side">
        {sel && (
          <>
            <div className="card">
              <h4>Классификация</h4>
              <dl>
                <dt>Тема</dt>
                <dd>{sel.category ? CATEGORY_LABELS[sel.category] : '—'}</dd>
                <dt>Приоритет</dt>
                <dd>
                  <span className={`prio ${sel.priority}`}>{PRIORITY_LABELS[sel.priority]}</span>
                </dd>
                <dt>Суть</dt>
                <dd>{sel.summary ?? '—'}</dd>
                <dt>Ожидание ответа</dt>
                <dd>
                  {fmtDuration(
                    ((sel.first_response_at ? new Date(sel.first_response_at) : new Date()).getTime() -
                      new Date(sel.created_at).getTime()) /
                      1000,
                  )}
                </dd>
              </dl>
            </div>
            {sel.routing && (
              <div className="card">
                <h4>Маршрутизация</h4>
                <p className="small">
                  <span className="tag">{sel.routing.strategy}</span> <span className="tag">{sel.routing.mode}</span>
                </p>
                <p className="small">{sel.routing.reason}</p>
              </div>
            )}
            {suggestion && (
              <div className="card suggestion">
                <h4>Подсказка агента <span className="tag">{suggestion.mode}</span></h4>
                <p>{suggestion.reply}</p>
                {suggestion.articles.length > 0 && (
                  <p className="small muted">База знаний: {suggestion.articles.map((a) => a.title).join(', ')}</p>
                )}
                <button onClick={() => setReply(suggestion.reply)}>Вставить в ответ</button>
              </div>
            )}
            {sel.quality && (
              <div className="card">
                <h4>Оценка качества <span className="tag">{sel.quality.mode}</span></h4>
                <div className="big">{sel.quality.score}/10</div>
                <p className="small">
                  {sel.quality.flags.map((f) => (
                    <span key={f} className="tag warn">
                      {FLAG_LABELS[f] ?? f}
                    </span>
                  ))}
                </p>
                <p className="small">{sel.quality.comment}</p>
              </div>
            )}
            {sel.rating && (
              <div className="card">
                <h4>Оценка клиента</h4>
                <div className="big">{'★'.repeat(sel.rating)}</div>
              </div>
            )}
          </>
        )}
      </aside>
    </div>
  )
}
