import { useState, useEffect, useCallback, useRef } from 'react'

const API = import.meta.env.VITE_API_URL || ''

function api(path, opts = {}) {
  return fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  }).then(r => {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
    return r.json()
  })
}

// ═══════════════════════════════════════════
// CHAT DRAWER
// ═══════════════════════════════════════════

function ChatDrawer({ onClose }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [listening, setListening] = useState(false)
  const [chatDate, setChatDate] = useState('')
  const [speakEnabled, setSpeakEnabled] = useState(true)
  const messagesEnd = useRef(null)
  const mediaRecorder = useRef(null)
  const audioChunks = useRef([])

  const scrollToBottom = () => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(scrollToBottom, [messages, loading])

  const speakText = useCallback(async (text) => {
    if (!speakEnabled) return
    try {
      const res = await fetch(`${API}/api/audio/speak`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text.slice(0, 500) }),
      })
      if (res.ok) {
        const blob = await res.blob()
        const url = URL.createObjectURL(blob)
        const audio = new Audio(url)
        audio.play()
        audio.onended = () => URL.revokeObjectURL(url)
      }
    } catch {
      // Fallback to browser speech
      if (window.speechSynthesis) {
        const u = new SpeechSynthesisUtterance(text.slice(0, 300))
        u.rate = 1.1
        window.speechSynthesis.speak(u)
      }
    }
  }, [speakEnabled])

  const sendMessage = useCallback(async (text) => {
    if (!text.trim()) return
    const userMsg = { role: 'user', content: text.trim() }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const data = await api('/api/chat', {
        method: 'POST',
        body: JSON.stringify({
          message: text.trim(),
          history: messages,
          date: chatDate || null,
        }),
      })
      const reply = { role: 'assistant', content: data.reply }
      setMessages(prev => [...prev, reply])
      speakText(data.reply)
    } catch (e) {
      setMessages(prev => [
        ...prev,
        { role: 'assistant', content: `Error: ${e.message}` },
      ])
    } finally {
      setLoading(false)
    }
  }, [messages, chatDate, speakText])

  const startListening = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream, { mimeType: 'audio/webm' })
      audioChunks.current = []
      mr.ondataavailable = (e) => audioChunks.current.push(e.data)
      mr.onstop = async () => {
        stream.getTracks().forEach(t => t.stop())
        const blob = new Blob(audioChunks.current, { type: 'audio/webm' })
        const form = new FormData()
        form.append('file', blob, 'recording.webm')
        try {
          const res = await fetch(`${API}/api/audio/transcribe`, {
            method: 'POST',
            body: form,
          })
          const data = await res.json()
          if (data.text) sendMessage(data.text)
        } catch (e) {
          console.error('Transcription failed:', e)
        }
      }
      mr.start()
      mediaRecorder.current = mr
      setListening(true)
    } catch (e) {
      console.error('Microphone access denied:', e)
    }
  }

  const stopListening = () => {
    if (mediaRecorder.current && mediaRecorder.current.state !== 'inactive') {
      mediaRecorder.current.stop()
    }
    setListening(false)
  }

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage(input)
    }
  }

  return (
    <div className="chat-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="chat-drawer">
        <div className="chat-header">
          <div>
            <h3>CHIMERA Data Analyst</h3>
            <input
              type="date"
              value={chatDate}
              onChange={(e) => setChatDate(e.target.value)}
              style={{ marginTop: 6, fontSize: 11, padding: '4px 8px' }}
              title="Scope conversation to a specific date"
            />
          </div>
          <div className="chat-header-actions">
            <button
              className={`btn btn-sm ${speakEnabled ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setSpeakEnabled(!speakEnabled)}
              title="Toggle voice responses"
            >
              {speakEnabled ? '🔊' : '🔇'}
            </button>
            <button className="btn btn-sm btn-ghost" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="empty" style={{ padding: 24 }}>
              Ask me anything about your session data, bets, performance, or patterns.
              {chatDate ? ` Focused on ${chatDate}.` : ''}
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`chat-msg chat-msg-${m.role}`}>
              {m.content}
            </div>
          ))}
          {loading && <div className="chat-loading">Analysing...</div>}
          <div ref={messagesEnd} />
        </div>

        <div className="chat-input-row">
          <button
            className={`btn-mic ${listening ? 'listening' : ''}`}
            onClick={listening ? stopListening : startListening}
            title={listening ? 'Stop recording' : 'Voice input'}
          >
            🎤
          </button>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Ask about sessions, bets, performance..."
            disabled={loading}
          />
          <button
            className="btn btn-primary btn-sm"
            onClick={() => sendMessage(input)}
            disabled={loading || !input.trim()}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════
// DASHBOARD TAB
// ═══════════════════════════════════════════

function DashboardTab() {
  const [summary, setSummary] = useState(null)
  const [engineState, setEngineState] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState('')

  const fetchData = useCallback(async () => {
    try {
      const [s, e] = await Promise.all([
        api('/api/summary'),
        api('/api/engine/state').catch(() => null),
      ])
      setSummary(s)
      setEngineState(e)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [fetchData])

  const handleSync = async () => {
    setSyncing(true)
    try {
      await api('/api/sync', { method: 'POST', body: JSON.stringify({}) })
      await fetchData()
    } catch (e) {
      setError(e.message)
    } finally {
      setSyncing(false)
    }
  }

  const rules = summary?.bets_by_rule || {}
  const maxRule = Math.max(1, ...Object.values(rules))

  return (
    <div>
      <div className="tab-toolbar">
        <h2>Dashboard</h2>
        <div className="tab-toolbar-actions">
          {engineState && (
            <span className={`badge badge-${engineState.status?.toLowerCase()}`}>
              Engine: {engineState.status}
            </span>
          )}
          <button className="btn btn-primary btn-sm" onClick={handleSync} disabled={syncing}>
            {syncing ? 'Syncing...' : 'Sync Now'}
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-value">{summary?.total_sessions ?? '—'}</div>
          <div className="stat-label">Sessions</div>
        </div>
        <div className="stat-card">
          <div className="stat-value accent">{summary?.total_bets ?? '—'}</div>
          <div className="stat-label">Total Bets</div>
        </div>
        <div className="stat-card">
          <div className="stat-value success">
            {summary?.total_stake != null ? `£${summary.total_stake.toFixed(2)}` : '—'}
          </div>
          <div className="stat-label">Total Stake</div>
        </div>
        <div className="stat-card">
          <div className="stat-value warning">
            {summary?.total_liability != null ? `£${summary.total_liability.toFixed(2)}` : '—'}
          </div>
          <div className="stat-label">Total Liability</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{summary?.total_markets ?? '—'}</div>
          <div className="stat-label">Markets Processed</div>
        </div>
        <div className="stat-card">
          <div className="stat-value success">{summary?.dry_run_bets ?? 0}</div>
          <div className="stat-label">Dry Run Bets</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{color:'var(--danger)'}}>{summary?.live_bets ?? 0}</div>
          <div className="stat-label">Live Bets</div>
        </div>
      </div>

      {Object.keys(rules).length > 0 && (
        <div className="panel glass">
          <h2>Rule Distribution</h2>
          <div className="rule-bars">
            {Object.entries(rules).sort((a,b) => b[1] - a[1]).map(([rule, count]) => (
              <div key={rule} className="rule-bar">
                <span className="rule-bar-label">{rule.replace(/_/g, ' ')}</span>
                <div className="rule-bar-track">
                  <div className="rule-bar-fill" style={{ width: `${(count / maxRule) * 100}%` }} />
                </div>
                <span className="rule-bar-count">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════
// SESSIONS TAB
// ═══════════════════════════════════════════

function SessionsTab() {
  const [sessions, setSessions] = useState([])
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [filterDate, setFilterDate] = useState('')

  const fetchSessions = useCallback(async () => {
    const params = filterDate ? `?date=${filterDate}` : ''
    const data = await api(`/api/sessions${params}`)
    setSessions(data.sessions || [])
  }, [filterDate])

  useEffect(() => { fetchSessions() }, [fetchSessions])

  const openDetail = async (sessionId) => {
    setSelected(sessionId)
    const data = await api(`/api/sessions/${sessionId}`)
    setDetail(data)
  }

  if (selected && detail) {
    const s = detail.session
    return (
      <div>
        <div className="tab-toolbar">
          <h2>Session: {selected}</h2>
          <button className="btn btn-ghost btn-sm" onClick={() => { setSelected(null); setDetail(null) }}>
            Back
          </button>
        </div>

        <div className="stat-grid" style={{ marginBottom: 20 }}>
          <div className="stat-card">
            <div className="stat-value">{s.total_bets}</div>
            <div className="stat-label">Bets</div>
          </div>
          <div className="stat-card">
            <div className="stat-value success">£{Number(s.total_stake).toFixed(2)}</div>
            <div className="stat-label">Stake</div>
          </div>
          <div className="stat-card">
            <div className="stat-value warning">£{Number(s.total_liability).toFixed(2)}</div>
            <div className="stat-label">Liability</div>
          </div>
          <div className="stat-card">
            <div className="stat-value accent">{s.markets_processed}</div>
            <div className="stat-label">Markets</div>
          </div>
        </div>

        {detail.bets?.length > 0 && (
          <>
            <h2>Bets ({detail.bets.length})</h2>
            <div className="table-wrap" style={{ marginBottom: 24 }}>
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Runner</th>
                    <th>Venue</th>
                    <th>Odds</th>
                    <th>Stake</th>
                    <th>Liability</th>
                    <th>Rule</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.bets.map((b, i) => (
                    <tr key={i}>
                      <td>{b.bet_timestamp ? new Date(b.bet_timestamp).toLocaleTimeString() : '—'}</td>
                      <td style={{ color: 'var(--text)' }}>{b.runner_name || '—'}</td>
                      <td>{b.venue || '—'}</td>
                      <td style={{ fontFamily: 'Lexend', fontWeight: 600 }}>{b.price}</td>
                      <td>£{Number(b.size).toFixed(2)}</td>
                      <td>£{Number(b.liability).toFixed(2)}</td>
                      <td><code>{b.rule_applied}</code></td>
                      <td>
                        <span className={`badge badge-${b.dry_run ? 'dry-run' : 'live'}`}>
                          {b.betfair_status || (b.dry_run ? 'DRY RUN' : 'LIVE')}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {detail.results?.length > 0 && (
          <>
            <h2>Rule Evaluations ({detail.results.length})</h2>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Race</th>
                    <th>Venue</th>
                    <th>Favourite</th>
                    <th>Odds</th>
                    <th>2nd Fav</th>
                    <th>Rule</th>
                    <th>Skipped</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.results.map((r, i) => (
                    <tr key={i} style={{ opacity: r.skipped ? 0.5 : 1 }}>
                      <td>{r.market_name || r.market_id}</td>
                      <td>{r.venue || '—'}</td>
                      <td style={{ color: 'var(--text)' }}>{r.favourite_name || '—'}</td>
                      <td style={{ fontFamily: 'Lexend', fontWeight: 600 }}>{r.favourite_odds}</td>
                      <td>{r.second_fav_name || '—'}</td>
                      <td><code>{r.rule_applied || '—'}</code></td>
                      <td>{r.skipped ? r.skip_reason || 'Yes' : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    )
  }

  return (
    <div>
      <div className="tab-toolbar">
        <h2>Sessions</h2>
        <div className="tab-toolbar-actions">
          <div className="date-filter">
            <label>Date:</label>
            <input type="date" value={filterDate} onChange={e => setFilterDate(e.target.value)} />
            {filterDate && (
              <button className="btn btn-ghost btn-sm" onClick={() => setFilterDate('')}>Clear</button>
            )}
          </div>
        </div>
      </div>

      {sessions.length === 0 ? (
        <div className="empty">No sessions found. Run a sync to pull data from the Lay Engine.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Session ID</th>
                <th>Mode</th>
                <th>Status</th>
                <th>Bets</th>
                <th>Stake</th>
                <th>Liability</th>
                <th>Markets</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map(s => (
                <tr key={s.session_id} className="clickable" onClick={() => openDetail(s.session_id)}>
                  <td>{s.date}</td>
                  <td style={{ color: 'var(--primary)', fontFamily: 'Lexend', fontWeight: 500 }}>{s.session_id}</td>
                  <td><span className={`badge badge-${s.mode === 'LIVE' ? 'live' : 'dry-run'}`}>{s.mode}</span></td>
                  <td><span className={`badge badge-${s.status?.toLowerCase()}`}>{s.status}</span></td>
                  <td>{s.total_bets}</td>
                  <td>£{Number(s.total_stake).toFixed(2)}</td>
                  <td>£{Number(s.total_liability).toFixed(2)}</td>
                  <td>{s.markets_processed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════
// REPORTS TAB
// ═══════════════════════════════════════════

function ReportsTab() {
  const [reports, setReports] = useState([])
  const [selectedReport, setSelectedReport] = useState(null)
  const [genDate, setGenDate] = useState(new Date().toISOString().split('T')[0])
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')

  const fetchReports = useCallback(async () => {
    const data = await api('/api/reports')
    setReports(data.reports || [])
  }, [])

  useEffect(() => { fetchReports() }, [fetchReports])

  // Poll for status updates when any report is generating
  useEffect(() => {
    if (reports.some(r => r.status === 'generating')) {
      const interval = setInterval(fetchReports, 5000)
      return () => clearInterval(interval)
    }
  }, [reports, fetchReports])

  const generate = async () => {
    setGenerating(true)
    setError('')
    try {
      await api('/api/reports/generate', {
        method: 'POST',
        body: JSON.stringify({ date: genDate }),
      })
      await fetchReports()
    } catch (e) {
      setError(e.message)
    } finally {
      setGenerating(false)
    }
  }

  const viewReport = async (id) => {
    const data = await api(`/api/reports/${id}`)
    setSelectedReport(data)
  }

  const downloadPdf = (id, date) => {
    window.open(`${API}/api/reports/${id}/pdf`, '_blank')
  }

  const deleteReport = async (id) => {
    await api(`/api/reports/${id}`, { method: 'DELETE' })
    setSelectedReport(null)
    fetchReports()
  }

  if (selectedReport) {
    const r = selectedReport
    const analysis = typeof r.analysis_json === 'string' ? JSON.parse(r.analysis_json) : r.analysis_json
    return (
      <div>
        <div className="tab-toolbar">
          <h2>{r.title}</h2>
          <div className="tab-toolbar-actions">
            {r.status === 'ready' && (
              <button className="btn btn-primary btn-sm" onClick={() => downloadPdf(r.id, r.date)}>
                Download PDF
              </button>
            )}
            <button className="btn btn-danger btn-sm" onClick={() => deleteReport(r.id)}>Delete</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setSelectedReport(null)}>Back</button>
          </div>
        </div>

        <span className={`badge badge-${r.status}`} style={{ marginBottom: 16, display: 'inline-block' }}>
          {r.status}
        </span>

        {analysis && (
          <div>
            {analysis.executive_summary && (
              <div className="report-section" style={{ borderLeft: '3px solid var(--primary)', marginBottom: 20 }}>
                <h3>Executive Summary</h3>
                <p style={{ color: 'var(--text)', fontWeight: 500 }}>{analysis.executive_summary}</p>
              </div>
            )}

            {[
              ['Odds Drift Patterns', analysis.odds_drift_patterns],
              ['Rule Distribution', analysis.rule_distribution?.analysis],
              ['Risk Exposure', analysis.risk_exposure],
              ['Venue Patterns', analysis.venue_patterns],
              ['Timing Observations', analysis.timing_observations],
              ['Anomalies', analysis.anomalies],
              ['Win/Loss Analysis', analysis.win_loss_analysis],
              ['Suggestions', analysis.suggestions],
              ['Additional Insights', analysis.additional_insights],
            ].filter(([, v]) => v).map(([title, text]) => (
              <div key={title} className="report-section">
                <h3>{title}</h3>
                <p>{text}</p>
              </div>
            ))}
          </div>
        )}

        {r.status === 'failed' && r.summary_text && (
          <div className="error">{r.summary_text}</div>
        )}
      </div>
    )
  }

  return (
    <div>
      <div className="tab-toolbar">
        <h2>Reports</h2>
        <div className="tab-toolbar-actions">
          <input type="date" value={genDate} onChange={e => setGenDate(e.target.value)} />
          <button className="btn btn-primary btn-sm" onClick={generate} disabled={generating}>
            {generating ? 'Starting...' : 'Generate Report'}
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {reports.length === 0 ? (
        <div className="empty">No reports yet. Generate one for a date that has session data.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Title</th>
                <th>Status</th>
                <th>Sessions</th>
                <th>Bets</th>
                <th>Stake</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {reports.map(r => (
                <tr key={r.id} className="clickable" onClick={() => r.status !== 'generating' && viewReport(r.id)}>
                  <td>{r.date}</td>
                  <td style={{ color: 'var(--text)' }}>{r.title}</td>
                  <td><span className={`badge badge-${r.status}`}>{r.status}</span></td>
                  <td>{r.sessions_count}</td>
                  <td>{r.bets_count}</td>
                  <td>£{Number(r.total_stake || 0).toFixed(2)}</td>
                  <td>{new Date(r.created_at).toLocaleDateString()}</td>
                  <td>
                    {r.status === 'ready' && (
                      <button className="btn btn-primary btn-sm" onClick={(e) => { e.stopPropagation(); downloadPdf(r.id, r.date) }}>
                        PDF
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════
// KNOWLEDGE TAB
// ═══════════════════════════════════════════

function KnowledgeTab() {
  const [entries, setEntries] = useState([])
  const [category, setCategory] = useState('')
  const [newCategory, setNewCategory] = useState('')
  const [newContent, setNewContent] = useState('')

  const fetchEntries = useCallback(async () => {
    const params = category ? `?category=${category}` : ''
    const data = await api(`/api/knowledge${params}`)
    setEntries(data.entries || [])
  }, [category])

  useEffect(() => { fetchEntries() }, [fetchEntries])

  const addEntry = async () => {
    if (!newCategory.trim() || !newContent.trim()) return
    await api('/api/knowledge', {
      method: 'POST',
      body: JSON.stringify({ category: newCategory, content: newContent }),
    })
    setNewCategory('')
    setNewContent('')
    fetchEntries()
  }

  const deleteEntry = async (id) => {
    await api(`/api/knowledge/${id}`, { method: 'DELETE' })
    fetchEntries()
  }

  const categories = [...new Set(entries.map(e => e.category))]

  return (
    <div>
      <div className="tab-toolbar">
        <h2>Knowledge Base</h2>
        <div className="tab-toolbar-actions">
          <select value={category} onChange={e => setCategory(e.target.value)} style={{ padding: '6px 12px', fontSize: 12 }}>
            <option value="">All Categories</option>
            {categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      </div>

      {/* Add entry form */}
      <div className="panel glass" style={{ marginBottom: 20 }}>
        <h2 style={{ fontSize: 12, marginBottom: 10 }}>Add Knowledge Entry</h2>
        <div className="form-row">
          <input
            type="text"
            value={newCategory}
            onChange={e => setNewCategory(e.target.value)}
            placeholder="Category (e.g. venue_pattern)"
            style={{ width: 200 }}
          />
          <input
            type="text"
            value={newContent}
            onChange={e => setNewContent(e.target.value)}
            placeholder="Insight or observation..."
            style={{ flex: 1 }}
          />
          <button className="btn btn-primary btn-sm" onClick={addEntry}>Add</button>
        </div>
      </div>

      {entries.length === 0 ? (
        <div className="empty">No knowledge entries yet. Generate a report to automatically extract insights.</div>
      ) : (
        entries.map(e => (
          <div key={e.id} className="knowledge-entry">
            <span className="knowledge-category">{e.category}</span>
            <div style={{ flex: 1 }}>
              <div className="knowledge-content">{e.content}</div>
              <div className="knowledge-meta">
                {e.source_type} {e.date_relevant && `| ${e.date_relevant}`} | {new Date(e.created_at).toLocaleDateString()}
              </div>
            </div>
            <button className="btn btn-ghost btn-sm" onClick={() => deleteEntry(e.id)} title="Delete">✕</button>
          </div>
        ))
      )}
    </div>
  )
}

// ═══════════════════════════════════════════
// SETTINGS TAB
// ═══════════════════════════════════════════

function SettingsTab() {
  const [syncStatus, setSyncStatus] = useState(null)
  const [pollInterval, setPollInterval] = useState(15)
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api('/api/sync/status').then(data => {
      setSyncStatus(data)
      if (data.scheduler) {
        setPollInterval(data.scheduler.poll_interval_minutes)
        setEnabled(data.scheduler.enabled)
      }
    }).catch(() => {})
  }, [])

  const saveConfig = async () => {
    setSaving(true)
    try {
      await api('/api/sync/configure', {
        method: 'POST',
        body: JSON.stringify({ poll_interval_minutes: pollInterval, enabled }),
      })
      const data = await api('/api/sync/status')
      setSyncStatus(data)
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h2>Settings</h2>

      <div className="settings-group">
        <h3>Scheduler Configuration</h3>
        <div className="settings-card">
          <div className="form-row">
            <span className="form-label">Enabled</span>
            <div className={`toggle ${enabled ? 'active' : ''}`} onClick={() => setEnabled(!enabled)} />
          </div>
          <div className="form-row">
            <span className="form-label">Poll Interval</span>
            <input
              type="number"
              value={pollInterval}
              onChange={e => setPollInterval(parseInt(e.target.value) || 15)}
              min={1}
              max={1440}
              style={{ width: 80 }}
            />
            <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>minutes</span>
          </div>
          <button className="btn btn-primary btn-sm" onClick={saveConfig} disabled={saving} style={{ marginTop: 12 }}>
            {saving ? 'Saving...' : 'Save Configuration'}
          </button>
        </div>
      </div>

      <div className="settings-group">
        <h3>Connection Status</h3>
        <div className="settings-card">
          <div className="form-row">
            <span className="form-label">Scheduler</span>
            <span style={{ color: syncStatus?.scheduler?.running ? 'var(--success)' : 'var(--danger)' }}>
              {syncStatus?.scheduler?.running ? 'Running' : 'Stopped'}
            </span>
          </div>
          {syncStatus?.scheduler?.jobs?.map(j => (
            <div key={j.id} className="form-row">
              <span className="form-label">Next Poll</span>
              <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{j.next_run}</span>
            </div>
          ))}
        </div>
      </div>

      {syncStatus?.recent_runs?.length > 0 && (
        <div className="settings-group">
          <h3>Sync History</h3>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Status</th>
                  <th>Sessions</th>
                  <th>Bets</th>
                  <th>Results</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                {syncStatus.recent_runs.map(r => (
                  <tr key={r.id}>
                    <td>{new Date(r.started_at).toLocaleString()}</td>
                    <td>
                      <span className={`badge badge-${r.status === 'success' ? 'completed' : r.status === 'failed' ? 'failed' : 'running'}`}>
                        {r.status}
                      </span>
                    </td>
                    <td>{r.sessions_synced}</td>
                    <td>{r.bets_synced}</td>
                    <td>{r.results_synced}</td>
                    <td style={{ color: 'var(--danger)', fontSize: 11, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {r.error_message || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════
// APP ROOT
// ═══════════════════════════════════════════

export default function App() {
  const [tab, setTab] = useState('dashboard')
  const [chatOpen, setChatOpen] = useState(false)
  const [health, setHealth] = useState(null)

  useEffect(() => {
    const check = () => api('/api/health').then(setHealth).catch(() => setHealth(null))
    check()
    const interval = setInterval(check, 30000)
    return () => clearInterval(interval)
  }, [])

  const dbConnected = health?.database === 'connected'

  return (
    <div className="dashboard">
      <header className="glass">
        <div className="header-left">
          <div>
            <h1>CHIMERA</h1>
            <div className="subtitle">Data Processor — Sessions</div>
          </div>
        </div>
        <div className="header-right">
          <div className="header-status">
            <div className={`status-dot ${dbConnected ? '' : 'disconnected'}`} />
            <span>{dbConnected ? 'Connected' : 'Disconnected'}</span>
          </div>
          <button className="btn btn-accent btn-sm" onClick={() => setChatOpen(true)}>
            AI Chat
          </button>
        </div>
      </header>

      <div className="tabs">
        {['dashboard', 'sessions', 'reports', 'knowledge', 'settings'].map(t => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      {tab === 'dashboard' && <DashboardTab />}
      {tab === 'sessions' && <SessionsTab />}
      {tab === 'reports' && <ReportsTab />}
      {tab === 'knowledge' && <KnowledgeTab />}
      {tab === 'settings' && <SettingsTab />}

      {chatOpen && <ChatDrawer onClose={() => setChatOpen(false)} />}
    </div>
  )
}
