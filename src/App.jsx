import { useState } from 'react'
import Upload from './components/Upload'
import QuestionsTable from './components/QuestionsTable'
import './App.css'

function App() {
  const [questions, setQuestions] = useState(null)
  const [fileName, setFileName] = useState('')
  const [clientName, setClientName] = useState('')
  const [isProcessing, setIsProcessing] = useState(false)

  const handleUpload = (parsedQuestions, name, client) => {
    setQuestions(parsedQuestions)
    setFileName(name)
    setClientName(client)
  }

  const handleStartFill = () => {
    setIsProcessing(true)
    simulateAgentFill()
  }

  const simulateAgentFill = () => {
    const indices = [...Array(questions.length).keys()]
    indices.sort(() => Math.random() - 0.5)

    indices.forEach((idx, i) => {
      setTimeout(() => {
        setQuestions(prev => {
          const updated = [...prev]
          updated[idx] = { ...updated[idx], status: 'filling' }
          return updated
        })
      }, i * 800)

      setTimeout(() => {
        setQuestions(prev => {
          const updated = [...prev]
          updated[idx] = {
            ...updated[idx],
            status: 'filled',
            generated_answer: getMockAnswer(),
            confidence: Math.random() * 0.5 + 0.5,
            citation: getMockCitation(),
          }
          return updated
        })
        if (i === indices.length - 1) {
          setTimeout(() => setIsProcessing(false), 1000)
        }
      }, i * 800 + 1500 + Math.random() * 2000)
    })
  }

  const handleReset = () => {
    setQuestions(null)
    setFileName('')
    setClientName('')
    setIsProcessing(false)
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>RFI Agent</h1>
        <span className="subtitle">Auto-fill RFIs for Avalere Health</span>
      </header>

      {!questions ? (
        <Upload onUpload={handleUpload} />
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

function getMockAnswer() {
  const answers = [
    "Avalere Health is a strategic advisory company providing market access, policy, and reimbursement solutions to life sciences companies. Founded in 2000, headquartered in Washington, D.C.",
    "Yes, Avalere Health maintains SOC 2 Type II certification and complies with GDPR requirements for all EU data subjects. Annual third-party audits are conducted.",
    "Avalere employs approximately 350 full-time staff with an attrition rate of 12% (2024). All staff complete mandatory compliance training annually.",
    "Avalere's anti-bribery and anti-corruption policy aligns with the UK Bribery Act and US FCPA. Annual training is mandatory for all employees.",
    "Avalere maintains a Business Continuity Plan (BCP) tested annually. RPO: 4 hours, RTO: 24 hours for critical systems.",
    "[NEEDS REVIEW] Insufficient context to answer this question confidently. Please consult the relevant internal documentation.",
  ]
  return answers[Math.floor(Math.random() * answers.length)]
}

function getMockCitation() {
  const citations = [
    "Company Information.html § Overview; Pfizer RFI 2024 Q12",
    "Data, information security.html § GDPR Compliance",
    "People Information.html § Headcount; Gilead RFI 2024 Q45",
    "Compliance.html § Anti-Bribery Policy",
    "Environmental, social, and governance.html § Sustainability",
    "",
  ]
  return citations[Math.floor(Math.random() * citations.length)]
}

export default App
