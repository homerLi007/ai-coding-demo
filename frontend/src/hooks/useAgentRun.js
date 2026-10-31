import { useCallback, useRef, useState } from "react";
import { runAgentStream } from "../api/agentClient";

export function useAgentRun() {
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState([]);
  const [prUrl, setPrUrl] = useState(null);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  const run = useCallback(async (params) => {
    setRunning(true);
    setEvents([]);
    setPrUrl(null);
    setError(null);
    abortRef.current = new AbortController();

    try {
      await runAgentStream(
        params,
        ({ type, data }) => {
          // 错误事件直接记录
          if (type === "error") {
            setError(data?.message || JSON.stringify(data));
            return;
          }
          // 正常事件累积（同时记录时间戳，便于展示）
          setEvents((prev) => [...prev, { type, data, at: Date.now() }]);
          // 捕获最终 PR 链接
          if (data?.final && data?.pr_url) {
            setPrUrl(data.pr_url);
          }
        },
        abortRef.current.signal
      );
    } catch (e) {
      if (e.name !== "AbortError") {
        setError(e.message || String(e));
      }
    } finally {
      setRunning(false);
    }
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { run, cancel, running, events, prUrl, error };
}