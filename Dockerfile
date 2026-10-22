# folio agent-server 生产镜像（201.14 prd / 19.12 JP 共用，环境差异全在 env_file）
# 与 AKS 生产镜像同构：slim + uv + 全量 COPY + uv sync --frozen --no-dev。
# 两个本服务特有依赖：
#   1. node：Pyodide 沙箱自起 node 孙进程（SANDBOX_NODE_BIN=node），
#      从官方镜像拷二进制，libstdc++6 是它的动态链接依赖。
#   2. third_party/ wheel：auth-core 已 wheel 化进仓（构建上下文自包含），
#      COPY third_party 必须在 uv sync 之前（uv.lock 指向 wheel 路径）。
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先拷依赖文件（利用缓存层）
COPY pyproject.toml uv.lock ./
COPY third_party/ ./third_party/

RUN uv sync --frozen --no-dev

# 再拷代码（.dockerignore 排除 .env* / .venv / logs 等）
COPY . .

EXPOSE 8002

CMD ["uv", "run", "--no-dev", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
