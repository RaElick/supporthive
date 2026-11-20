import type { WsEvent } from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export const tokens = {
  get operator() {
    return localStorage.getItem('sh_operator_token')
  },
  set operator(v: string | null) {
    if (v) localStorage.setItem('sh_operator_token', v)
    else localStorage.removeItem('sh_operator_token')
  },
  get widget() {
    return localStorage.getItem('sh_widget_token')
  },
  set widget(v: string | null) {
    if (v) localStorage.setItem('sh_widget_token', v)
    else localStorage.removeItem('sh_widget_token')
  },
}

export async function api<T = any>(
  path: string,
  opts: { method?: string; body?: unknown; token?: string | null } = {},
): Promise<T> {
  const token = opts.token === undefined ? tokens.operator : opts.token
  const res = await fetch(path, {
    method: opts.method ?? (opts.body !== undefined ? 'POST' : 'GET'),
    headers: {
      ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  })
  if (!res.ok) {
    let msg = res.statusText
    try {
      const j = await res.json()
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg)
  }
  return res.json()
}

/** WebSocket with automatic reconnect. Returns a function that closes it. */
export function subscribe(path: string, token: string, onEvent: (e: WsEvent) => void, onOpen?: () => void) {
  let ws: WebSocket | null = null
  let closed = false
  let retry = 500
  const connect = () => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}${path}?token=${encodeURIComponent(token)}`)
    ws.onopen = () => {
      retry = 500
      onOpen?.()
    }
    ws.onmessage = (m) => onEvent(JSON.parse(m.data))
    ws.onclose = (e) => {
      if (closed || e.code === 4401) return
      setTimeout(connect, retry)
      retry = Math.min(retry * 2, 8000)
    }
  }
  connect()
  return () => {
    closed = true
    ws?.close()
  }
}

export function fmtDuration(s: number | null | undefined): string {
  if (s == null) return '—'
  if (s < 60) return `${Math.round(s)} с`
  if (s < 3600) return `${Math.round(s / 60)} мин`
  return `${(s / 3600).toFixed(1)} ч`
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}

export function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}
