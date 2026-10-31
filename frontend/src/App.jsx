import { useState } from "react";
import { useAgentRun } from "./hooks/useAgentRun";

export default function App() {
  // 表单字段（与后端 RunRequest 保持一致）
  const [task, setTask] = useState(
    "Create a function that takes a list of file paths and deletes them safely."
  );
  const [owner, setOwner] = useState("homerLi007");
  const [repo, setRepo] = useState("ai-coding-demo");
  const [branch, setBranch] = useState("main");

  const { run, cancel, running, events, prUrl, error } = useAgentRun();

  const onSubmit = (e) => {
    e.preventDefault();
    if (running) return;
    run({
      task,
      github_owner: owner,
      github_repo: repo,
      github_base_branch: branch,
    });
  };

  return (
    <div style={styles.page}>
      <h1 style={styles.h1}>🤖 Multi-Agent Runner</h1>

      {/* ─── 输入表单 ─── */}
      <form onSubmit={onSubmit} style={styles.form}>
        <label style={styles.label}>
          <span>Task</span>
          <textarea
            rows={3}
            value={task}
            onChange={(e) => setTask(e.target.value)}
            disabled={running}
            style={styles.input}
          />
        </label>

        <div style={styles.row}>
          <label style={styles.label}>
            <span>GitHub Owner</span>
            <input
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
              disabled={running}
              style={styles.input}
            />
          </label>
          <label style={styles.label}>
            <span>Repo</span>
            <input
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              disabled={running}
              style={styles.input}
            />
          </label>
          <label style={styles.label}>
            <span>Base Branch</span>
            <input
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              disabled={running}
              style={styles.input}
            />
          </label>
        </div>

        <div style={styles.actions}>
          <button type="submit" disabled={running} style={styles.btnPrimary}>
            {running ? "⏳ Running..." : "🚀 Run Agent"}
          </button>
          {running && (
            <button
              type="button"
              onClick={cancel}
              style={styles.btnDanger}
            >
              ✋ Cancel
            </button>
          )}
        </div>
      </form>

      {/* ─── 错误提示 ─── */}
      {error && (
        <div style={styles.errorBox}>
          ❌ {error}
        </div>
      )}

      {/* ─── 最终 PR 链接 ─── */}
      {prUrl && (
        <div style={styles.successBox}>
          ✅ 完成！PR 已创建：{" "}
          <a href={prUrl} target="_blank" rel="noreferrer" style={styles.link}>
            {prUrl}
          </a>
        </div>
      )}

      {/* ─── 实时事件流 ─── */}
      <h2 style={styles.h2}>📡 实时进度（{events.length} 条）</h2>
      <div style={styles.logBox}>
        {events.length === 0 && !running && (
          <p style={{ color: "#888" }}>还没有事件，点 Run 开始。</p>
        )}
        {events.map((e, i) => {
          const d = e.data || {};
          return (
            <div key={i} style={styles.logLine}>
              <span style={styles.logTime}>
                {new Date(e.at).toLocaleTimeString()}
              </span>
              <span style={styles.logNode}>
                {d.node || e.type}
              </span>
              {d.iteration != null && (
                <span style={styles.badge}>iter {d.iteration}</span>
              )}
              {d.fix_attempts != null && d.fix_attempts > 0 && (
                <span style={styles.badge}>fix#{d.fix_attempts}</span>
              )}
              {d.needs_fix === true && (
                <span style={styles.badgeWarn}>needs_fix</span>
              )}
              {d.needs_fix === false && d.node === "security" && (
                <span style={styles.badgeOk}>passed</span>
              )}
              {d.next_agent && (
                <span style={styles.badgeNext}>→ {d.next_agent}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── 行内样式（避免额外依赖 Tailwind 等） ───
const styles = {
  page: {
    maxWidth: 960,
    margin: "0 auto",
    padding: 24,
    fontFamily: "system-ui, -apple-system, Segoe UI, monospace",
    color: "#222",
  },
  h1: { fontSize: 22, marginBottom: 16 },
  h2: { fontSize: 16, marginTop: 24, marginBottom: 8 },
  form: { display: "grid", gap: 12 },
  row: { display: "flex", gap: 12 },
  label: { display: "flex", flexDirection: "column", gap: 4, flex: 1 },
  input: {
    padding: "8px 10px",
    border: "1px solid #ccc",
    borderRadius: 6,
    fontSize: 14,
    fontFamily: "inherit",
  },
  actions: { display: "flex", gap: 8 },
  btnPrimary: {
    padding: "10px 20px",
    background: "#2563eb",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    cursor: "pointer",
    fontSize: 14,
  },
  btnDanger: {
    padding: "10px 20px",
    background: "#dc2626",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    cursor: "pointer",
    fontSize: 14,
  },
  errorBox: {
    marginTop: 12,
    padding: 12,
    background: "#fef2f2",
    border: "1px solid #fecaca",
    borderRadius: 6,
    color: "#b91c1c",
  },
  successBox: {
    marginTop: 12,
    padding: 12,
    background: "#f0fdf4",
    border: "1px solid #bbf7d0",
    borderRadius: 6,
  },
  link: { color: "#2563eb", textDecoration: "underline" },
  logBox: {
    background: "#0f172a",
    color: "#e2e8f0",
    padding: 12,
    borderRadius: 6,
    maxHeight: 400,
    overflow: "auto",
    fontFamily: "ui-monospace, Menlo, Consolas, monospace",
    fontSize: 13,
  },
  logLine: {
    display: "flex",
    gap: 8,
    alignItems: "center",
    padding: "4px 0",
    borderBottom: "1px solid #1e293b",
  },
  logTime: { color: "#64748b", fontSize: 11, minWidth: 80 },
  logNode: { color: "#38bdf8", fontWeight: 600, minWidth: 90 },
  badge: {
    fontSize: 11,
    padding: "2px 6px",
    background: "#1e293b",
    borderRadius: 4,
    color: "#cbd5e1",
  },
  badgeWarn: {
    fontSize: 11,
    padding: "2px 6px",
    background: "#7c2d12",
    borderRadius: 4,
    color: "#fed7aa",
  },
  badgeOk: {
    fontSize: 11,
    padding: "2px 6px",
    background: "#14532d",
    borderRadius: 4,
    color: "#bbf7d0",
  },
  badgeNext: {
    fontSize: 11,
    padding: "2px 6px",
    background: "#1e3a8a",
    borderRadius: 4,
    color: "#bfdbfe",
  },
};