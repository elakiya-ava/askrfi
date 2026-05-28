function QuestionsTable({ questions }) {
  const sheets = [...new Set(questions.map(q => q.sheet_name))]

  return (
    <div className="table-container">
      {sheets.map(sheet => {
        const sheetQs = questions.filter(q => q.sheet_name === sheet)
        const filled = sheetQs.filter(q => q.status === 'filled').length
        const filling = sheetQs.filter(q => q.status === 'filling').length

        return (
          <div key={sheet} className="sheet-group">
            <div className="sheet-header">
              <h3>{sheet}</h3>
              <span className="sheet-progress">
                {filled}/{sheetQs.length} filled
                {filling > 0 && <span className="filling-badge"> · {filling} in progress</span>}
              </span>
            </div>
            <table className="questions-table">
              <thead>
                <tr>
                  <th className="col-num">#</th>
                  <th className="col-question">Question</th>
                  <th className="col-answer">Answer</th>
                  <th className="col-confidence">Confidence</th>
                  <th className="col-citation">Citation</th>
                  <th className="col-status">Status</th>
                </tr>
              </thead>
              <tbody>
                {sheetQs.map((q, i) => (
                  <tr key={q.id} className={`row-${q.status}${q.status === 'filled' ? ` confidence-${getConfidenceClass(q.confidence)}` : ''}`}>
                    <td className="col-num">{i + 1}</td>
                    <td className="col-question">
                      <div className="q-text">{q.question_text}</div>
                      {q.existing_answer && (
                        <div className="existing-answer">
                          <span className="label">Existing:</span> {q.existing_answer}
                        </div>
                      )}
                    </td>
                    <td className="col-answer">
                      {q.status === 'filling' && (
                        <div className="filling-indicator">
                          <div className="spinner"></div>
                          <span>Generating...</span>
                        </div>
                      )}
                      {q.status === 'filled' && (
                        <div className="generated-answer">
                          {q.generated_answer}
                          {q.generated_answer && <div className="char-count">{q.generated_answer.length} chars</div>}
                        </div>
                      )}
                      {q.status === 'pending' && (
                        <div className="pending-placeholder">—</div>
                      )}
                    </td>
                    <td className="col-confidence">
                      {q.confidence !== null && q.confidence !== undefined && (
                        <>
                          <div className={`confidence-badge ${getConfidenceClass(q.confidence)}`}>
                            {Math.round(q.confidence * 100)}%
                          </div>
                          {q.confidence < 0.5 && (
                            <div className="needs-review-badge">NEEDS REVIEW</div>
                          )}
                        </>
                      )}
                      {q.status === 'filling' && (
                        <div className="spinner small"></div>
                      )}
                    </td>
                    <td className="col-citation">
                      {q.citation && <div className="citation-text">{q.citation}</div>}
                    </td>
                    <td className="col-status">
                      <StatusBadge status={q.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      })}
    </div>
  )
}

function StatusBadge({ status }) {
  const config = {
    pending: { icon: '⏳', label: 'Pending' },
    filling: { icon: '⚡', label: 'Filling' },
    filled: { icon: '✅', label: 'Done' },
    error: { icon: '❌', label: 'Error' },
  }
  const { icon, label } = config[status] || config.pending

  return (
    <span className={`status-badge status-${status}`}>
      {icon} {label}
    </span>
  )
}

function getConfidenceClass(confidence) {
  if (confidence >= 0.8) return 'high'
  if (confidence >= 0.5) return 'medium'
  return 'low'
}

export default QuestionsTable
