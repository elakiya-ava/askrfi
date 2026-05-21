import { useState, useRef, useEffect } from 'react'
import Upload from './components/Upload'
import QuestionsTable from './components/QuestionsTable'
import './App.css'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function App() {
  const [questions, setQuestions] = useState(null)
  const [fileName, setFileName] = useState('')
  const [clientName, setClientName] = useState('')
  const [sessionId, setSessionId] = useState(null)
  const [isProcessing, setIsProcessing] = useState(false)

  const handleUpload = (parsedQuestions, name, client, sessId) => {
    setQuestions(parsedQuestions)
    setFileName(name)
    setClientName(client)
    setSessionId(sessId)
  }

  const handleStartFill = () => {
    if (!sessionId) return
    setIsProcessing(true)

    // Mark all as filling
    setQuestions(prev => prev.map(q => ({ ...q, status: 'filling' })))

    // Connect to SSE stream (use mock fill for demo)
    const evtSource = new EventSource(`${API_BASE}/api/fill-mock/${sessionId}`)

    evtSource.addEventListener('progress', (e) => {
      const data = JSON.parse(e.data)
      setQuestions(prev => {
        const updated = [...prev]
        const idx = data.index
        if (idx >= 0 && idx < updated.length) {
          updated[idx] = {
            ...updated[idx],
            status: data.status === 'filled' ? 'filled' : data.status === 'pending' ? 'filling' : 'error',
            generated_answer: data.generated_answer || '',
            confidence: data.confidence,
            citation: data.citation || '',
          }
        }
        return updated
      })
    })

    evtSource.addEventListener('done', () => {
      evtSource.close()
      setIsProcessing(false)
    })

    evtSource.addEventListener('error', () => {
      evtSource.close()
      setIsProcessing(false)
    })
  }

  const handleReview = async () => {
    if (!sessionId) return
    setIsProcessing(true)
    try {
      const res = await fetch(`${API_BASE}/api/review/${sessionId}`, { method: 'POST' })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setQuestions(data.questions.map(q => ({
        ...q,
        status: q.fill_status === 'filled' ? 'filled' : q.fill_status || 'pending',
      })))
    } catch (err) {
      console.error('Review failed:', err)
    } finally {
      setIsProcessing(false)
    }
  }

  const handleDownload = async (format = 'excel') => {
    if (!sessionId) return
    try {
      const endpoints = {
        csv: `${API_BASE}/api/download-csv/${sessionId}`,
        pdf: `${API_BASE}/api/download-pdf/${sessionId}`,
        excel: `${API_BASE}/api/download/${sessionId}`,
      }
      const endpoint = endpoints[format] || endpoints.excel
      const res = await fetch(endpoint)
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Download failed' }))
        alert(err.detail || 'Download failed')
        return
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const stem = fileName.replace(/\.(xlsx|xlsm)$/i, '')
      if (format === 'csv') {
        a.download = stem + '_FILLED.csv'
      } else if (format === 'pdf') {
        a.download = stem + '_FILLED.pdf'
      } else {
        const ext = fileName.match(/\.(xlsx|xlsm)$/i)?.[0] || '.xlsx'
        a.download = stem + '_FILLED' + ext
      }
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err) {
      alert('Download failed — is the backend running?')
    }
  }

  const handleReset = () => {
    setQuestions(null)
    setFileName('')
    setClientName('')
    setSessionId(null)
    setIsProcessing(false)
  }

  const allFilled = questions && questions.every(q => q.status === 'filled')

  return (
    <div className="app">
      <header className="app-header">
        <h1>RFI Agent</h1>
        <span className="subtitle">Auto-fill RFIs for Avalere Health</span>
      </header>

      {!questions ? (
        <Upload onUpload={handleUpload} apiBase={API_BASE} />
      ) : (
        <div className="workspace">
          <div className="toolbar">
            <div className="file-info">
              <span className="file-name">{fileName}</span>
              {clientName && <span className="client-badge">{clientName}</span>}
              <span className="q-count">{questions.length} questions</span>
            </div>
            <div className="toolbar-actions">
              {!isProcessing && questions.some(q => q.status === 'pending') && (
                <button className="btn-fill" onClick={handleStartFill}>
                  ▶ Fill with AI
                </button>
              )}
              {!isProcessing && allFilled && (
                <DownloadDropdown onDownload={handleDownload} />
              )}
              <button className="btn-reset" onClick={handleReset}>
                ✕ New File
              </button>
            </div>
          </div>
          <QuestionsTable questions={questions} />
        </div>
      )}
    </div>
  )
}

function DownloadDropdown({ onDownload }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleSelect = (format) => {
    setOpen(false)
    onDownload(format)
  }

  return (
    <div className="download-dropdown" ref={ref}>
      <button className="btn-download" onClick={() => setOpen(!open)}>
        ⬇ Download ▾
      </button>
      {open && (
        <ul className="download-menu">
          <li onClick={() => handleSelect('excel')}>📊 Excel (.xlsx)</li>
          <li onClick={() => handleSelect('csv')}>📄 CSV (.csv)</li>
          <li onClick={() => handleSelect('pdf')}>📑 PDF (.pdf)</li>
        </ul>
      )}
    </div>
  )
}

export default App
