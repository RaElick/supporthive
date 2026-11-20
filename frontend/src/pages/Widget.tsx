import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fmtTime, subscribe, tokens } from '../api'
import type { Message, Ticket } from '../types'

/** Demo storefront with the floating support chat widget. */
export default function Widget() {
  const [open, setOpen] = useState(true)
  const [name, setName] = useState('')
  const [token, setToken] = useState(tokens.widget)
  const [ticket, setTicket] = useState<Ticket | null>(null)
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const bottom = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    if (!token) return
    try {
      const r = await api<{ ticket: Ticket | null }>('/api/widget/ticket', { token })
      setTicket(r.ticket)
    } catch (e: any) {
      if (e.status === 401) {
        tokens.widget = null
        setToken(null)
      }
    }
  }, [token])

  useEffect(() => {
    load()
    if (!token) return
    return subscribe('/ws/widget', token, (e) => {
      if (e.type === 'message') {
        const m = e.data as Message
        setTicket((t) => {
          if (!t || t.id !== m.ticket_id) {
            load()
            return t
          }
          if (t.messages?.some((x) => x.id === m.id)) return t
          return { ...t, messages: [...(t.messages ?? []), m] }
        })
      } else if (e.type.startsWith('ticket_')) {
        setTicket((t) => (t && t.id === e.data.id ? { ...t, ...e.data, messages: t.messages } : t))
      }
    }, load)
  }, [token, load])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [ticket?.messages?.length, open])

  const start = async () => {
    const r = await api<{ token: string }>('/api/widget/session', { body: { name: name || null }, token: null })
    tokens.widget = r.token
    setToken(r.token)
  }

  const send = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim() || !token) return
    setError('')
    try {
      const r = await api<{ ticket_id: number; message: Message; new_ticket: boolean }>('/api/widget/messages', {
        body: { text },
        token,
      })
      setText('')
      if (r.new_ticket || !ticket || ticket.id !== r.ticket_id) await load()
      else
        setTicket((t) =>
          t && !t.messages?.some((x) => x.id === r.message.id) ? { ...t, messages: [...(t.messages ?? []), r.message] } : t,
        )
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const rate = async (n: number) => {
    if (!ticket || !token) return
    await api('/api/widget/rate', { body: { ticket_id: ticket.id, rating: n }, token })
    setTicket({ ...ticket, rating: n })
  }

  const closed = ticket?.status === 'closed'
  const statusLine = !ticket
    ? 'Обычно отвечаем за пару минут'
    : ticket.status === 'assigned'
      ? `Вам отвечает ${ticket.operator_name}`
      : ticket.status === 'closed'
        ? 'Обращение закрыто'
        : 'Подбираем специалиста…'

  return (
    <div className="shop">
      <header className="shop-header">
        <b>ТехноМаркет</b>
        <span className="muted">Электроника с доставкой</span>
      </header>
      <main className="shop-grid">
        {['Смартфон X12', 'Наушники Air', 'Ноутбук Pro 14', 'Умные часы S', 'Планшет Tab 11', 'Колонка Mini'].map((p, i) => (
          <div className="card product" key={p}>
            <div className="product-img" style={{ filter: `hue-rotate(${i * 50}deg)` }} />
            <b>{p}</b>
            <span>{(19990 + i * 7000).toLocaleString('ru-RU')} ₽</span>
          </div>
        ))}
      </main>

      <div className={`widget ${open ? 'open' : ''}`}>
        {open ? (
          <div className="widget-panel">
            <div className="widget-head">
              <div>
                <b>Поддержка</b>
                <div className="small">{statusLine}</div>
              </div>
              <button className="ghost light" onClick={() => setOpen(false)} aria-label="Свернуть">
                ✕
              </button>
            </div>
            {!token ? (
              <div className="widget-body pad">
                <p>Здравствуйте! Как к вам обращаться?</p>
                <input placeholder="Имя (необязательно)" value={name} onChange={(e) => setName(e.target.value)} />
                <button onClick={start}>Начать чат</button>
              </div>
            ) : (
              <>
                <div className="widget-body">
                  {(ticket?.messages ?? []).map((m) => (
                    <div key={m.id} className={`bubble ${m.sender === 'customer' ? 'mine' : m.sender}`}>
                      {m.text}
                      <span className="time">{fmtTime(m.created_at)}</span>
                    </div>
                  ))}
                  {!ticket && <p className="muted pad">Опишите ваш вопрос — мы поможем.</p>}
                  {closed && (
                    <div className="rate">
                      {ticket?.rating ? (
                        <span>Спасибо за оценку: {'★'.repeat(ticket.rating)}</span>
                      ) : (
                        <>
                          <span>Оцените качество обслуживания:</span>
                          <div>
                            {[1, 2, 3, 4, 5].map((n) => (
                              <button key={n} className="star" onClick={() => rate(n)}>
                                ★<small>{n}</small>
                              </button>
                            ))}
                          </div>
                        </>
                      )}
                    </div>
                  )}
                  <div ref={bottom} />
                </div>
                <form className="widget-input" onSubmit={send}>
                  <input
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    placeholder={closed ? 'Новый вопрос…' : 'Сообщение…'}
                  />
                  <button type="submit" disabled={!text.trim()}>
                    ➤
                  </button>
                </form>
                {error && <div className="error pad">{error}</div>}
              </>
            )}
          </div>
        ) : (
          <button className="widget-fab" onClick={() => setOpen(true)}>
            💬 Поддержка
          </button>
        )}
      </div>
    </div>
  )
}
