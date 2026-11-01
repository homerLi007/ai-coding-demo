FROM python:3.13-slim
WORKDIR /app

# ── 装 JDK 17 + Maven + Node 20（用于真实执行测试）──
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jdk-headless \
    maven \
    nodejs \
    npm \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── 装 Playwright + Chromium（用于 E2E 测试）──
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium \
    && rm -rf /root/.cache/pip

# npm 走国内镜像
RUN npm config set registry https://registry.npmmirror.com

# pip 走国内镜像
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY supervisor_multi_agent.py .

COPY --from=frontend-builder /frontend/dist ./frontend/dist

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]