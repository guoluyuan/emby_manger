# 配置说明

本页对 EmbyPulse 的核心配置项进行解释，并提供适合小白用户的选项建议。

> ✅ 提示：以下字段全部来自系统设置与通知机器人页面的真实表单，不会出现未上线的配置项。

## 0. 配置位置总览（按页面分类）

| 页面 | 设置区块 | 配置项 |
| --- | --- | --- |
| 系统设置 | 媒体服务器核心 | `server_type` / `emby_host` / `emby_api_key` / `playback_data_mode` |
| 系统设置 | 对外服务门户 | `emby_public_url` / `client_download_url` / `welcome_message` |
| 系统设置 | MoviePilot 自动化联控 | `moviepilot_url` / `moviepilot_token` / `pulse_url` |
| 系统设置 | TMDB & 数据库体检 | `tmdb_api_key` / `proxy_url` |
| 系统设置 | Webhook 安全凭证 | `webhook_token` |
| 系统设置 | 隐藏黑名单 | `hidden_users` |
| 通知与机器人 | 推送场景总控 | `enable_bot` / `enable_notify` / `enable_library_notify` |
| 通知与机器人 | Telegram 通道 | `tg_bot_token` / `tg_chat_id` |
| 通知与机器人 | 企业微信(推送) | `wecom_corpid` / `wecom_agentid` / `wecom_corpsecret` / `wecom_touser` |
| 通知与机器人 | 企微交互指令回调 | `wecom_token` / `wecom_aeskey` |
| 通知与机器人 | 企业微信 API 代理 | `wecom_proxy_url` |
| 缺集管理页面 | 底层接管配置弹窗 | `client_type` / `client_url` / `client_user` / `client_pass` |

## 1. Emby 基础配置

- `server_type`：服务器类型（Emby / Jellyfin）
- `emby_host`：Emby 服务地址，例如 `http://127.0.0.1:8096`
- `emby_api_key`：Emby 后台生成的 API Key
- `webhook_token`：Webhook 安全校验令牌，需与 Emby Webhook 地址中的 `token` 保持一致
- `emby_public_url`：对外访问 Emby 的公网地址，用于生成跳转链接

## 2. 播放统计配置（重点）

- `playback_data_mode`：播放数据模式，支持 `sqlite` 或 `api`
- `DB_PATH`：本地模式下 Playback Reporting 数据库文件路径
- `hidden_users`：需要在大盘中隐藏的用户 ID 列表

提示：Docker 部署可用环境变量 `PLAYBACK_DATA_MODE=api|sqlite` 直接指定模式，适合快速切换。

### 🧩 模式选择（小白必看）

**API 模式（推荐：无法挂载数据库时）**
- 适合：极空间、群晖、云服务器、容器里拿不到数据库文件的环境
- 优点：部署最省心，只要填 `EMBY_API_KEY` 即可启动
- 注意：需要安装 Emby 官方 Playback Reporting 插件（两种模式都需要）

**本地数据库模式（推荐：可挂载数据库时）**
- 适合：本地 Docker 或能挂载 Emby 数据目录的 NAS
- 优点：查询性能更高，统计更及时
- 注意：必须正确填写 `DB_PATH`，且容器内要能访问该文件

## 3. Telegram 配置

- `tg_bot_token`：Telegram Bot Token
- `tg_chat_id`：接收主动通知的目标聊天 ID
- `proxy_url`：Telegram 网络代理（可选）

可用能力：
- 播放开始 / 停止推送
- 入库通知推送
- 报表推送
- 机器人指令交互

## 4. 企业微信配置

- `wecom_corpid`：企业 ID
- `wecom_corpsecret`：应用 Secret
- `wecom_agentid`：应用 AgentId
- `wecom_touser`：默认推送目标，通常可填 `@all`
- `wecom_proxy_url`：企业微信 API 地址，默认 `https://qyapi.weixin.qq.com`
- `wecom_token`：回调 Token（可选）
- `wecom_aeskey`：Encoding AESKey（可选）

可用能力：
- 文本与图文通知
- 自定义菜单
- 播放与入库事件推送

## 5. MoviePilot 配置

- `moviepilot_url`：MoviePilot 服务地址
- `moviepilot_token`：MoviePilot API Token
- `pulse_url`：Pulse 审批回跳地址（通常填当前系统公网地址）

可用能力：
- 缺集搜索
- 一键下发下载任务
- 与缺集管理联动完成补货流程

## 6. 下载器截胡配置

- `client_type`：下载器类型
- `client_url`：下载器地址，例如 `http://127.0.0.1:8080`
- `client_user`：下载器账号
- `client_pass`：下载器密码

可用能力：
- 季包推送后自动锁定下载任务
- 根据目标集数筛出 wanted 文件
- 自动剔除非目标集文件，实现精准补集
