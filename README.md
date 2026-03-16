# EmbyPulse（映迹）

本仓库为二次修改版本，基于原项目 https://github.com/zeyu8023/emby-pulse 调整。

## 变更概览（精简）

主要调整：
- 前端资源本地化，避免 CDN 被拦截导致空白
- 去重/榜单/播放记录展示修复与优化
- 用户社区跳转与内外网智能切换优化
- 心愿工坊防重复提交与性能优化

近期修复：
- 邀请系统支持默认模板用户并强制选择模板生成
- 邀请注册统一策略克隆（包含 IsHidden*），管理员模板被过滤/拒绝
- 邀请入口地址写入系统配置，自动连通测试与安全校验
- 10308 用户端根路径与 /api/register 放行修复
- 启动时数据库目录/文件自动创建并打印提示
- 播放数据引擎自动切换：无库自动回退 API
- 邀请码长度提升
- 数据洞察详情页恢复美观样式并保留性能优化
- 终端分布/播放软件偏好改为扇形图并修复图例中文
- 最近 100 条流水账取消虚拟占位，避免底部空白
- 10308 用户端放行 /api/csrf 并自动重试 CSRF
- “我的”页图表切换标签后稳定渲染（含 rAF 兜底）
- 工单大厅“前往用户前台”公网访问时跳转到系统配置的公网入口
- MoviePilot 下载器自动拉取与选择，缺集补齐下发时显式携带 downloader/save_path

## 🚀 快速部署

### Docker Compose（推荐）

```yaml
version: '3.8'
services:
  emby-pulse:
    image: mp740429299/emby_manger:latest
    container_name: emby-manger
    restart: unless-stopped
    network_mode: host #默认端口号为10307
    volumes:
      - ./config:/app/config
      - /path/to/emby/data:/emby-data # API 模式下可不挂载数据库
    environment:
      - TZ=Asia/Shanghai
      - PLAYBACK_DATA_MODE=api # api 或 sqlite
      - DB_PATH=/emby-data/playback_reporting.db # sqlite 模式必填
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
  emby-pulse:
    image: mp740429299/emby_manger:latest
    container_name: emby-manger
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./config:/app/config
    environment:
      - TZ=Asia/Shanghai
      - PLAYBACK_DATA_MODE=api
```

本地模式（sqlite）：

```yaml
version: '3.8'
services:
  emby-pulse:
    image: mp740429299/emby_manger:latest
    container_name: emby-manger
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./config:/app/config
      - /path/to/emby/data:/emby-data:ro
    environment:
      - TZ=Asia/Shanghai
      - PLAYBACK_DATA_MODE=sqlite
      - DB_PATH=/emby-data/playback_reporting.db
```

**常见报错（API 模式误用 / 本地模式未挂载）**

- 若日志出现 `找不到文件: /emby-data/playback_reporting.db` 或播放数据一直为 0，说明你处于 `sqlite` 模式但未正确挂载数据库。
- 解决办法：要么切换 `PLAYBACK_DATA_MODE=api`，要么正确挂载 Emby 数据目录并填写 `DB_PATH`。

**如何确认 Playback Reporting 插件已启用**

1. 进入 Emby 控制台 → 插件 → “Playback Reporting”。
2. 确认插件已启用并处于运行状态。
3. 若首次安装无历史数据，至少产生一次播放记录后再刷新页面。

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

Windows：右键 `start-windows.ps1` → 使用 PowerShell 打开

说明：启动脚本会自动构建 Tailwind CSS 资源、确保工作目录正确，并避免重复启动进程；直接 `python run.py` 可能导致样式未生成或多实例冲突。

4. 首次访问 `http://localhost:10307/` 在网页中完成配置（Emby 地址与 API Key）

## ⚙️ 配置说明

以下为部署后建议优先检查的核心配置项：

### Emby 基础配置

- `emby_host`：Emby 服务器地址，例如 `http://127.0.0.1:8096`
- `emby_api_key`：Emby 后台生成的 API Key
- `webhook_token`：Webhook 安全校验令牌，需与 Emby Webhook 地址中的 `token` 保持一致
- `emby_public_url`：对外访问 Emby 的公网地址，用于生成跳转链接

### 播放统计配置

- `playback_data_mode`：播放数据模式，支持 `sqlite` 或 `api`
- `DB_PATH`：本地模式下 Playback Reporting 数据库文件路径
- `hidden_users`：需要在大盘中隐藏的用户 ID 列表

#### 🧩 两种模式如何选择（小白必看）

**API 模式（推荐：不方便挂载数据库时）**
- 适合：极空间、群晖、云服务器、容器里拿不到数据库文件的环境
- 优点：部署最省心，只要填 `EMBY_API_KEY` 即可启动
- 注意：需要安装 Emby 官方 Playback Reporting 插件（两种模式都需要）

**本地数据库模式（推荐：可挂载数据库时）**
- 适合：本地 Docker 或能挂载 Emby 数据目录的 NAS
- 优点：查询性能更高，统计更及时
- 注意：必须正确填写 `DB_PATH`，且容器内要能访问该文件

说明：
- `sqlite` 模式直接读取数据库文件，性能更高，适合本地 Docker 挂载场景
- `api` 模式通过 Emby 插件接口穿透查询，部署最轻量，适合无法挂载数据库的环境

### Telegram 配置

- `tg_bot_token`：Telegram Bot Token
- `tg_chat_id`：接收主动通知的目标聊天 ID
- `proxy_url`：Telegram 网络代理，可选

支持能力：
- 播放开始 / 停止推送
- 入库通知推送
- 报表推送
- 机器人指令交互

### 企业微信配置

- `wecom_corpid`：企业 ID
- `wecom_corpsecret`：应用 Secret
- `wecom_agentid`：应用 AgentId
- `wecom_touser`：默认推送目标，通常可填 `@all`
- `wecom_proxy_url`：企业微信 API 地址，默认 `https://qyapi.weixin.qq.com`

支持能力：
- 文本与图文通知
- 自定义菜单
- 播放与入库事件推送

### MoviePilot 配置

- `moviepilot_url`：MoviePilot 服务地址
- `moviepilot_token`：MoviePilot API Token
- `moviepilot_downloader`：MoviePilot 下载器名称（从 MP 端拉取选择）
- `moviepilot_save_path`：MoviePilot 默认保存路径（可自动读取并回填）

支持能力：
- 缺集搜索
- 一键下发下载任务
- 与缺集管理联动完成补货流程

### 下载器截胡配置

当前支持：
- qBittorrent
- Transmission

常用配置项：
- `client_type`：下载器类型
- `client_url`：下载器地址，例如 `http://127.0.0.1:8080`
- `client_user`：下载器账号
- `client_pass`：下载器密码

支持能力：
- 季包推送后自动锁定下载任务
- 根据目标集数筛出 wanted 文件
- 自动剔除非目标集文件，实现精准补集

## 📄 许可证与开源协议

本项目基于 MIT 许可证开源，并保留原项目署名与仓库链接。
