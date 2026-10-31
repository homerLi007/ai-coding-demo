# ---------- 阶段 1：构建前端 ----------
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：构建后端 + 托管前端 ----------
FROM python:3.13-slim
WORKDIR /app

# 系统依赖（PyGithub 等一般不需要，保留最小化）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && rm -rf /var/lib/apt/lists/*

# 后端依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 后端源码
COPY backend/ ./backend/
COPY supervisor_multi_agent.py .

# 前端构建产物
COPY --from=frontend-builder /frontend/dist ./frontend/dist

EXPOSE 8000

# 健康检查（对应 backend/main.py 里的 /api/health）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]