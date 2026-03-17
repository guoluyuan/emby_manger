本仓库为 EmbyPulse（映迹） 二次修改版本，基于原项目 https://github.com/zeyu8023/emby-pulse 调整。

## 变更概览（精简）

主要调整：
- 网页初始化配置添加EMBY_HOST和EMBY_API设置，方便配置
- 管理端和用户端全局跳转emby网页时，智能判断内外网，内网走内网ip地址，外网走公网地址
- 添加管理端网页自动/手动检测容器版本更新，一键更新(有给禁止检测更新开关)
- 添加自定义管理端和用户端PC和移动端登录壁纸，模糊度可调
- 前端资源本地化，避免 CDN 被拦截导致空白
- 去重/榜单/播放记录展示修复与优化
- 用户社区影片可直接跳转emby网页端
- 心愿工坊防重复提交
- 风险监控增加监控频率和自动封禁开关

近期修复：
- 邀请系统支持默认模板用户并强制选择模板生成
- 邀请注册统一策略克隆，邀请可生成内网和外网邀请链接，管理员模板被过滤/拒绝
- 邀请入口地址写入系统配置，自动连通测试与安全校验
- 播放数据引擎自动切换：无本地数据库文件自动回退 API模式，避免启动失败
- 数据洞察详情页恢复美观样式并保留性能优化
- 终端分布/播放软件偏好改为扇形图并修复图例中文
- 最近 100 条流水账取消虚拟占位，避免底部空白
- MoviePilot 下载器自动拉取与选择，缺集补齐下发时显式携带 downloader/save_path

## 🚀 快速部署

### Docker Compose（推荐）

> 若你使用的是旧版 `docker-compose`（v1），项目名默认取 **当前目录名**。建议把部署目录命名为 `emby-manger`，即可避免与已有项目冲突；或使用命令行 `docker-compose -p emby-manger up -d` 指定项目名。

```yaml
version: '3.8'
services:
  emby-manger:
    image: mp740429299/emby_manger:latest
    container_name: emby-manger
    restart: unless-stopped
    network_mode: host # 默认端口号为 10307
    volumes:
      - /path/to/emby/data:/emby-data # API 模式下可不挂载数据库
      - /path/to/emby-manger/config:/app/config # 配置持久化（避免更新后重新初始化）
      # 可选：启用网页一键更新
      # - /var/run/docker.sock:/var/run/docker.sock
      # - /path/to/emby-manger/:/compose
    environment:
      - TZ=Asia/Shanghai
      - PLAYBACK_DATA_MODE=api # api 或 sqlite
      - DB_PATH=/emby-data/playback_reporting.db # sqlite 模式必填
      # 可选：网页一键更新
      # - DOCKER_UPDATE_COMPOSE_FILES=/compose/docker-compose.yml
      # - DOCKER_UPDATE_NAME=emby-manger
```

首次安装后，请访问 `http://localhost:10307/` 在网页中填写 Emby 地址与 API Key（无需写入 `docker-compose.yml`）。

**模式选择说明**

- `PLAYBACK_DATA_MODE=api`：API 模式，不强制挂载数据库文件；可移除 `DB_PATH` 和 `emby-data` 挂载。
- `PLAYBACK_DATA_MODE=sqlite`：本地模式，必须挂载 Emby 数据目录并正确填写 `DB_PATH`。

**双模式示例（直接拷贝用）**

API 模式：

```yaml
version: '3.8'
services:
  emby-manger:
    image: mp740429299/emby_manger:latest
    container_name: emby-manger
    restart: unless-stopped
    network_mode: host
    volumes:
      - /path/to/emby-manger/config:/app/config # 配置持久化（避免更新后重新初始化）
      # 可选：启用网页一键更新
      # - /var/run/docker.sock:/var/run/docker.sock
      # - /path/to/emby-manger/:/compose
    environment:
      - TZ=Asia/Shanghai
      - PLAYBACK_DATA_MODE=api
      # 可选：网页一键更新
      # - DOCKER_UPDATE_COMPOSE_FILES=/compose/docker-compose.yml
      # - DOCKER_UPDATE_NAME=emby-manger
```

本地模式（sqlite）：

```yaml
version: '3.8'
services:
  emby-manger:
    image: mp740429299/emby_manger:latest
    container_name: emby-manger
    restart: unless-stopped
    network_mode: host
    volumes:
      - /path/to/emby/data:/emby-data:ro
      - /path/to/emby-manger/config:/app/config # 配置持久化（避免更新后重新初始化）
      # 可选：启用网页一键更新
      # - /var/run/docker.sock:/var/run/docker.sock
      # - /path/to/emby-manger/:/compose
    environment:
      - TZ=Asia/Shanghai
      - PLAYBACK_DATA_MODE=sqlite
      - DB_PATH=/emby-data/playback_reporting.db
      # 可选：网页一键更新
      # - DOCKER_UPDATE_COMPOSE_FILES=/compose/docker-compose.yml
      # - DOCKER_UPDATE_NAME=emby-manger
```

**常见报错（API 模式误用 / 本地模式未挂载）**

- 若日志出现 `找不到文件: /emby-data/playback_reporting.db` 或播放数据一直为 0，说明你处于 `sqlite` 模式但未正确挂载数据库。
- 解决办法：要么切换 `PLAYBACK_DATA_MODE=api`，要么正确挂载 Emby 数据目录并填写 `DB_PATH`。

**如何确认 Playback Reporting 插件已启用**

1. 进入 Emby 控制台 → 插件 → “Playback Reporting”。
2. 确认插件已启用并处于运行状态。
3. 若首次安装无历史数据，至少产生一次播放记录后再刷新页面。

### 容器手动更新（Docker）

```bash
cd docker-compose.yml所在目录
docker-compose down
docker-compose pull
docker-compose up -d
```

### 网页一键更新（docker.sock 方式）

适用于想在管理后台直接点击更新的场景，需要宿主机把 Docker 控制权交给容器。

**前置条件**
- 挂载 ` /var/run/docker.sock `
- 容器内有 `docker` 命令（本镜像已内置）
- 如果使用 docker compose：需要把 compose 文件目录挂载进容器，并设置环境变量
  - `DOCKER_UPDATE_COMPOSE_FILES`：容器内的 compose 文件路径
  - `DOCKER_UPDATE_NAME`：项目名/服务名/容器名（推荐三者一致，减少配置）
  - `DOCKER_UPDATE_HELPER_IMAGE`：可选，更新器镜像（默认优先 `docker:25-cli` / `docker:24-cli`，会自动安装 compose 插件）

**示例（在 compose 中启用）**

```yaml
services:
  emby-manger:
    image: mp740429299/emby_manger:latest
    container_name: emby-manger
    restart: unless-stopped
    network_mode: host
    volumes:
      - /path/to/emby/data:/emby-data:ro
      - /path/to/emby-manger/config:/app/config # 配置持久化（避免更新后重新初始化）
      - /var/run/docker.sock:/var/run/docker.sock
      - /path/to/emby-manger/:/compose
    environment:
      - DOCKER_UPDATE_COMPOSE_FILES=/compose/docker-compose.yml
      - DOCKER_UPDATE_NAME=emby-manger
```

启用后，进入「系统设置」即可看到“容器一键更新”卡片。

### 本地部署（非 Docker）

1. 安装 Python 与 pip（建议使用系统自带或官方安装包）
2. 在项目根目录安装依赖：

```bash
python -m pip install -r requirements.txt
```

3. 启动服务：

Linux：

```bash
./start-ubuntu.sh
```

Windows：
右键 `start-windows.ps1` → 使用 PowerShell 打开

说明：启动脚本会自动构建 Tailwind CSS 资源、确保工作目录正确，并避免重复启动进程；直接 `python run.py` 可能导致样式未生成或多实例冲突。

4. 首次访问 `http://localhost:10307/` 在网页中完成配置（Emby 地址与 API Key）


## 📄 许可证与开源协议

本项目基于 MIT 许可证开源，并保留原项目署名与仓库链接。
