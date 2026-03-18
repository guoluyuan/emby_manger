FROM node:18-alpine AS assets

WORKDIR /app

COPY package.json package-lock.json ./
COPY tailwind.config.js tailwind.request.config.js ./
COPY static/css/tailwind-input.css static/css/tailwind-input.css
COPY templates templates
COPY static/js static/js

RUN npm ci && npm run build:css

# 使用官方 Python 轻量镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 设置时区 (很少变动，放在最前面)
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装基础字体 + Docker CLI（避免发布无 docker 的镜像）
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg fonts-dejavu-core \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# 构建时校验 docker CLI 是否可用（避免发布无 docker 的镜像）
RUN docker --version && docker compose version

# 1. 先复制依赖文件并安装 (只要 requirements.txt 不变，这里就会完美命中缓存，瞬间跳过！)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 🔥 核心修复：把动态版本参数移到耗时的依赖安装之后！
ARG APP_VERSION=1.2.0.80
ENV APP_VERSION=${APP_VERSION}

# 2. 复制所有项目文件到容器 (这里改了 HTML 才会重新复制，但不会重新 pip install)
COPY . .

# 2.1 复制构建后的 CSS（避免 Docker 构建缺少样式）
COPY --from=assets /app/static/css/admin.css /app/static/css/admin.css
COPY --from=assets /app/static/css/request-tailwind.css /app/static/css/request-tailwind.css

# 3. 创建配置和数据挂载点
RUN mkdir -p /app/config /emby-data && chmod -R 777 /app/config /emby-data

# 4. 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10307"]
