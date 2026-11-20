export type Sender = 'customer' | 'operator' | 'system'

export interface Message {
  id: number
  ticket_id: number
  sender: Sender
  operator_id: number | null
  text: string
  created_at: string
}

export interface Ticket {
  id: number
  channel: 'widget' | 'telegram'
  status: 'new' | 'queued' | 'assigned' | 'closed'
  category: string | null
  priority: 'low' | 'normal' | 'high'
  summary: string | null
  operator_id: number | null
  operator_name: string | null
  customer: { id: number; name: string }
  created_at: string
  assigned_at: string | null
  first_response_at: string | null
  closed_at: string | null
  rating: number | null
  resolved: boolean | null
  messages?: Message[]
  last_message?: Message
  routing?: { strategy: string; mode: string; reason: string } | null
  quality?: {
    score: number
    politeness: number
    resolved: boolean
    flags: string[]
    comment: string
    mode: string
  } | null
}

export interface Operator {
  id: number
  login: string
  name: string
  is_admin: boolean
  skills: string[]
  max_concurrent: number
  online: boolean
  load?: number
}

export interface WsEvent {
  type: string
  data: any
}

export const CATEGORY_LABELS: Record<string, string> = {
  billing: 'Оплата',
  delivery: 'Доставка',
  technical: 'Техническая проблема',
  account: 'Аккаунт и доступ',
  returns: 'Возврат и обмен',
  general: 'Общий вопрос',
}

export const PRIORITY_LABELS: Record<string, string> = { low: 'низкий', normal: 'обычный', high: 'высокий' }
export const STATUS_LABELS: Record<string, string> = {
  new: 'новое',
  queued: 'в очереди',
  assigned: 'в работе',
  closed: 'закрыто',
}
export const FLAG_LABELS: Record<string, string> = {
  no_greeting: 'нет приветствия',
  rude: 'грубость',
  unresolved: 'не решено',
  slow_first_response: 'долгий первый ответ',
  no_operator_reply: 'нет ответа оператора',
  short_answers: 'односложные ответы',
}
