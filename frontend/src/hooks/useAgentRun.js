import { useCallback, useRef, useState } from "react";
import { runAgentStream } from "../api/agentClient";

function newTaskId() {
  if (crypto?.randomUUID) return crypto.randomUUID();
  return "task-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

export function useAgentRun() {
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState([]);
  const [prUrl, setPrUrl] = useState(null);
  const [error, setError] = useState(null);
  const [appUrl, setAppUrl] = useState(null);
  const [repoUrl, setRepoUrl] = useState(null);

  // ── 新增 state ──
  const [taskId, setTaskId] = useState(
    () => sessionStorage.getItem("current_task_id")
  );
  const [status, setStatus] = useState("idle"); // idle | running | done | error

  const abortRef = useRef(null);

  // ── 新增：显式新任务 ──
  const newTask = useCallback(() => {
    sessionStorage.removeItem("current_task_id");
    setTaskId(null);
    setEvents([]);
    setPrUrl(null);
    setAppUrl(null);
    setRepoUrl(null);
    setError(null);
    setStatus("idle");
  }, []);

  const run = useCallback(async (params) => {
    setRunning(true);
    setStatus("running");
    setEvents([]);
    setPrUrl(null);
    setError(null);
    setAppUrl(null);
    setRepoUrl(null);

    // ── 新增：确保 task_id 存在，并同步到 state ──
    let id = sessionStorage.getItem("current_task_id");
    if (!id) {
      id = newTaskId();
      sessionStorage.setItem("current_task_id", id);
    }
    setTaskId(id);

    abortRef.current = new AbortController();

    try {
      await runAgentStream(
        { ...params, task_id: id },   // ← 把 id 显式传给 api 层
        ({ type, data }) => {
          if (type === "error") {
            setError(data?.message || JSON.stringify(data));
            setStatus("error");
            return;
          }

          setEvents((prev) => [...prev, { type, data, at: Date.now() }]);

          if (data?.final && data?.pr_url) setPrUrl(data.pr_url);
          if (data?.app_url) setAppUrl(data.app_url);
          if (data?.generated_repo_url) setRepoUrl(data.generated_repo_url);

          if (type === "done") {
            setStatus("done");
            // 如果选“自动清理”方案，取消下面两行注释：
            // sessionStorage.removeItem("current_task_id");
            // setTaskId(null);
          }
        },
        abortRef.current.signal
      );
    } catch (e) {
      if (e.name !== "AbortError") {
        setError(e.message || String(e));
        setStatus("error");
      }
    } finally {
      setRunning(false);
    }
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setStatus("idle");
  }, []);

  return {
    run,
    cancel,
    newTask,      // ← 新增
    running,
    events,
    prUrl,
    error,
    appUrl,
    repoUrl,
    taskId,       // ← 新增
    status,       // ← 新增
  };
}