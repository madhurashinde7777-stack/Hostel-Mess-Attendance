import { useState } from 'react'
import { useAuth } from '../auth.jsx'
import { Icon, Logo } from '../icons.jsx'
import { Button } from '../ui.jsx'

const DEMO = [
  { role: 'Student', username: 's001', password: 'pass123', icon: 'utensils' },
  { role: 'Rector', username: 'rector', password: 'rector123', icon: 'shield' },
  { role: 'Mess', username: 'mess', password: 'mess123', icon: 'chart' },
]

const FEATURES = [
  { icon: 'check', title: 'Tap to skip a meal', text: 'Students mark lunch and dinner from their phone.' },
  { icon: 'bolt', title: 'Counts update live', text: 'The mess sees the exact number of plates to cook.' },
  { icon: 'shield', title: 'No more roll calls', text: 'The rector sees who is in, out or at home at a glance.' },
]

export default function Login() {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
    } catch (err) {
      setError(err.message)
      setLoading(false)
    }
  }

  return (
    <div className="login">
      <section className="login-art">
        <div className="login-brand">
          <Logo size={44} />
          <div>
            <b>MessMate</b>
            <small>Hostel mess attendance</small>
          </div>
        </div>

        <div className="login-hero">
          <span className="eyebrow">For girls' hostels</span>
          <h1>
            Every plate counted.
            <br />
            <em>No roll calls.</em>
          </h1>
          <p>Students mark their meals, the count updates by itself and the mess always knows how much to cook.</p>
          <ul className="features">
            {FEATURES.map((f) => (
              <li key={f.title}>
                <span className="feature-icon">
                  <Icon name={f.icon} size={16} />
                </span>
                <div>
                  <b>{f.title}</b>
                  <span>{f.text}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>

        {/* decorative thali plates */}
        <svg className="plates" viewBox="0 0 400 400" aria-hidden="true">
          <circle cx="300" cy="300" r="150" />
          <circle cx="300" cy="300" r="110" />
          <circle cx="300" cy="300" r="46" />
          <circle cx="232" cy="236" r="18" />
          <circle cx="368" cy="236" r="18" />
          <circle cx="300" cy="392" r="18" />
          <circle cx="60" cy="70" r="40" />
          <circle cx="60" cy="70" r="24" />
        </svg>
      </section>

      <section className="login-panel">
        <form className="login-card" onSubmit={submit}>
          <div className="login-mobile-brand">
            <Logo size={40} />
            <b>MessMate</b>
          </div>
          <h2>Welcome back</h2>
          <p className="muted">Sign in to continue</p>

          {error && (
            <div className="alert alert-error">
              <Icon name="alert" size={16} /> {error}
            </div>
          )}

          <label className="field">
            <span className="field-label">Username</span>
            <div className="input-icon">
              <Icon name="users" size={16} />
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="e.g. s001"
                autoFocus
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck="false"
                required
              />
            </div>
          </label>
          <label className="field">
            <span className="field-label">Password</span>
            <div className="input-icon">
              <Icon name="lock" size={16} />
              <input
                type={show ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoCapitalize="none"
                required
              />
              <button type="button" className="input-action" onClick={() => setShow(!show)} aria-label="Show password">
                <Icon name={show ? 'eyeOff' : 'eye'} size={16} />
              </button>
            </div>
          </label>

          <Button variant="primary" size="lg" className="block" loading={loading} icon="login">
            Sign in
          </Button>

          {import.meta.env.DEV && (
          <div className="demo">
            <span>Try a demo account</span>
            <div className="demo-chips">
              {DEMO.map((d) => (
                <button
                  type="button"
                  key={d.role}
                  className="demo-chip"
                  onClick={() => {
                    setUsername(d.username)
                    setPassword(d.password)
                    setError('')
                  }}
                >
                  <Icon name={d.icon} size={14} /> {d.role}
                </button>
              ))}
            </div>
          </div>
          )}
        </form>
      </section>
    </div>
  )
}