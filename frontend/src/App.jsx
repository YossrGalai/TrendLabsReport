import { useEffect, useState } from 'react'
import axios from 'axios'

function App() {
  const [status, setStatus] = useState('chargement...')

  useEffect(() => {
    axios.get('http://localhost:8000/health/db')
      .then(res => setStatus(`${res.data.status} (db: ${res.data.database})`))
      .catch(() => setStatus('erreur de connexion au backend'))
  }, [])

  return (
    <div style={{ padding: '2rem', fontFamily: 'sans-serif' }}>
      <h1>TrendLabs Reporting Tool</h1>
      <p>Statut backend : <strong>{status}</strong></p>
    </div>
  )
}

export default App
