const API_BASE = "http://localhost:8000";

/**
 * 消费后端 SSE 流。
 *
 * 后端事件格式：
 *   data: {"node": "coder", "iteration": 1, ...}\n\n
 *   event: done\ndata: {}\n\n
 *   event: error\ndata: {"message": "..."}\n\n
 */
export async function runAgentStream(params, onEvent, signal) {
  const response = await fetch(`${API_BASE}/api/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
    signal,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP ${response.status}: ${text}`);
  }
  if (!response.body) {
    throw new Error("当前浏览器不支持 ReadableStream，请升级浏览器");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      // stream:true 让多字节 UTF-8 字符可以跨 chunk 拼接
      buffer += decoder.decode(value, { stream: true });

      // SSE 事件以空行分隔
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);

        let eventType = "message";
        const dataLines = [];
        for (const line of rawEvent.split("\n")) {
          if (line.startsWith("event:")) {
            eventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trimStart());
          }
        }
        if (dataLines.length === 0) continue;

        const dataStr = dataLines.join("\n");
        let data;
        try {
          data = JSON.parse(dataStr);
        } catch {
          data = dataStr;
        }
        onEvent({ type: eventType, data });
      }
    }
  } finally {
    reader.releaseLock();
  }
}