# OPS (ops-ai) 私有化镜像 —— fnOS NAS 容器化步骤一
# 双跑验证版：与 Windows 实例（192.168.1.44:8000）并行，数据卷独立
FROM python:3.14-slim

# 系统依赖：sqlite(内置)、ssh client（部署验证）、curl（健康检查）
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python 依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码（排除 venv/data/.git，见 .dockerignore）
COPY . /app

# 数据卷：DB / 密钥 / 日志 / matrix crypto store / 上传
ENV APP_DATA_DIR=/data
RUN mkdir -p /data
VOLUME ["/data"]

# 时区
ENV TZ=Asia/Shanghai

# 健康检查：MCP 网关进程活着即可（不做深度依赖检查，避免误杀）
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -s -o /dev/null http://127.0.0.1:8000/healthz | grep -q "ok" || exit 1

EXPOSE 8000

# 单进程模式（与 Windows 侧一致：main.py __main__ 分支起 uvicorn，PORT/HOST 环境变量控制）
CMD ["python", "main.py"]
