import { useCallback, useRef, useState } from "react";
import { runAgentStream } from "../api/agentClient";

export function useAgentRun() {
  const [running, setRunning] = useState(false);
  const [events, setEvents] = useState([]);
  const [prUrl, setPrUrl] = useState(null);
  const [error, setError] = useState(null);

  // 👇👇👇 新增：两行 state 定义，和上面几个 useState 并列 👇👇👇
  const [appUrl, setAppUrl] = useState(null);
  const [repoUrl, setRepoUrl] = useState(null);
  // 👆👆👆 新增结束 👆👆👆

  const abortRef = useRef(null);

  const run = useCallback(async (params) => {
    setRunning(true);
    setEvents([]);
    setPrUrl(null);
    setError(null);
    // 👇 顺手把新增的 state 也重置（可选但推荐）
    setAppUrl(null);
    setRepoUrl(null);
    // 👆
    abortRef.current = new AbortController();

    try {
      await runAgentStream(
        params,
        ({ type, data }) => {
          if (type === "error") {
            setError(data?.message || JSON.stringify(data));
            return;
          }

          setEvents((prev) => [...prev, { type, data, at: Date.now() }]);

          if (data?.final && data?.pr_url) {
            setPrUrl(data.pr_url);
          }

          // 👇👇👇 新增：在 onEvent 回调里捕获 app_url / repo_url 👇👇👇
          if (data?.app_url) {
            setAppUrl(data.app_url);
          }
          if (data?.generated_repo_url) {
            setRepoUrl(data.generated_repo_url);
          }
          // 👆👆👆 新增结束 👆👆👆
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

  // 👇👇👇 修改：return 里加上 appUrl 和 repoUrl 👇👇👇
  return {
    run,
    cancel,
    running,
    events,
    prUrl,
    error,
    appUrl,
    repoUrl,
  };
  // 👆👆👆 修改结束 👆👆👆
}