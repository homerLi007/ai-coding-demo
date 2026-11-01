import { useState } from 'react';

export default function App() {
  const [input, setInput] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setResults([]);
    const paths = input
      .split('\n')
      .map((p) => p.trim())
      .filter(Boolean);
    if (paths.length === 0) {
      setError('Enter at least one path.');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch('/api/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paths })
      });
      if (!res.ok) throw new Error('Request failed: ' + res.status);
      const data = await res.json();
      setResults(data.results || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="container">
      <h1>Delete Files</h1>
      <form onSubmit={handleSubmit}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="One file path per line"
          rows={6}
        />
        <button type="submit" disabled={loading}>
          {loading ? 'Deleting...' : 'Delete'}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {results.length > 0 && (
        <ul className="results">
          {results.map((r, i) => (
            <li key={i} className={r.deleted ? 'ok' : 'fail'}>
              <strong>{r.path}</strong>: {r.deleted ? 'deleted' : r.error}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
