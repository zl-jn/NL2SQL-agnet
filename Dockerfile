# Dockerfile for NL2SQL Agent

FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN set -eux; \
    apt-get update -o Acquire::Retries=5; \
    apt-get install -y --no-install-recommends \
      gcc \
      g++ \
      libpq-dev \
      default-libmysqlclient-dev \
    || (apt-get update -o Acquire::Retries=5 && apt-get install -y --no-install-recommends --fix-missing \
      gcc g++ libpq-dev default-libmysqlclient-dev); \
    rm -rf /var/lib/apt/lists/*


# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY nl2sql_agent.py .

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health')"

# 启动命令
CMD ["python", "nl2sql_agent.py"]