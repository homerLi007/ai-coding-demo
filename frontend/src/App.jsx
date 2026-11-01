import { useState } from 'react';

export default function App() {
  const [input, setInput] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleDelete() {
    const paths = input
      .split('\n')
      .map((p) => p.trim())
      .filter((p) => p.length > 0);

    if (paths.length === 0) {
      setError('Please enter at least one path.');
      return;
    }

    setLoading(true);
    setError(null);
    setResults([]);

    try {
      const res = await fetch('/api/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths })
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      setResults(data.results || []);
    } catch (e) {
      setError(e.message || 'Request failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="container">
      <h1>File Deleter</h1>
      <p className="hint">Enter one file path per line (relative to server working directory).</p>
      <textarea
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder="e.g.\ntmp/a.txt\ntmp/b.txt"
        rows={8}
      />
      <button onClick={handleDelete} disabled={loading}>
        {loading ? 'Deleting...' : 'Delete Files'}
      </button>
      {error && <div className="error">{error}</div>}
      {results.length > 0 && (
        <ul className="results">
          {results.map((r, i) => (
            <li key={i} className={r.deleted ? 'ok' : 'fail'}>
              <span className="path">{r.path}</span>
              <span className="status">
                {r.deleted ? 'deleted' : 'failed: ' + (r.error || 'unknown')}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
