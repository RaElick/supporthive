import { Link } from 'react-router-dom'

export default function Home() {
  return (
    <div className="home">
      <h1>SupportHive</h1>
      <p className="muted">Мультиагентная CRM клиентской поддержки</p>
      <div className="home-cards">
        <Link to="/shop" className="card home-card">
          <h3>Клиент</h3>
          <p>Демо-магазин с виджетом чата. Напишите обращение — его классифицирует и назначит агент.</p>
        </Link>
        <Link to="/desk" className="card home-card">
          <h3>Оператор</h3>
          <p>Рабочее место: очередь, диалоги, подсказки агента-помощника.</p>
          <code>anna / igor / maria / dmitry / olga — demo1234</code>
        </Link>
        <Link to="/admin" className="card home-card">
          <h3>Администратор</h3>
          <p>Дашборды, стратегии маршрутизации, трассировка агентов, база знаний.</p>
          <code>admin — admin1234</code>
        </Link>
      </div>
    </div>
  )
}
