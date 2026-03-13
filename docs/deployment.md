# 部署、升级与故障排查

本页覆盖：快速部署、升级流程、常见问题与故障排查建议。

> ✅ 提示：Emby 地址与 API Key 是登录与数据获取的核心前置条件，请优先确认。

## 1. 快速部署（Docker）

### 1.1 使用 docker compose

```yaml
version: '3.8'
services:
  emby-pulse:
    image: guogai/emby_manger:latest
    container_name: emby-pulse
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./config:/app/config
      - /path/to/emby/data:/emby-data # API 模式下可不挂载数据库
    environment:
      - TZ=Asia/Shanghai
      - DB_PATH=/emby-data/playback_reporting.db # 本地模式必填
      - EMBY_HOST=http://127.0.0.1:8096
      - EMBY_API_KEY=xxxxxxxxxxxxxxxxxxxxx
```

### 1.3 部署注意事项（新手必读）

1. **必须填写 Emby 地址，否则无法登录**
   - `EMBY_HOST` 必填，例如 `http://127.0.0.1:8096`
   - 登录账号密码就是你的 Emby 管理员账号密码

2. **必须安装 Playback Reporting 插件**
   - 无论 API 模式还是本地模式，都需要该插件生成播放统计数据

3. **本地数据库模式一定要挂载数据库路径**
   - 容器内 `DB_PATH` 必须指向 **容器内可访问的数据库文件**
   - 如果没挂载或路径写错，统计会一直是空

4. **数据库路径如何找到**
   - 数据库文件名通常为 `playback_reporting.db`
   - 在 Emby 数据目录下搜索该文件即可定位
   - 常见位置（供参考）：`.../plugins/Playback Reporting/playback_reporting.db`

5. **数据库如何挂载（示例）**
   - 假设宿主机路径为 `/path/to/emby/data`，容器内映射为 `/emby-data`
   - `volumes` 中写：`/path/to/emby/data:/emby-data`
   - `DB_PATH` 写：`/emby-data/plugins/Playback Reporting/playback_reporting.db`

## 2. 升级与迁移

### 2.1 升级流程

1. 停止旧容器
2. 拉取最新镜像
3. 重新启动容器

```bash
docker pull guogai/emby_manger:latest
docker compose down
docker compose up -d
```

### 2.2 配置与数据迁移

- 你的配置文件存放在 `./config` 目录
- 升级时只需保留 `./config` 即可自动继承旧配置
- 如果使用本地数据库模式，确保 `DB_PATH` 指向的数据库路径保持不变

## 3. 常见问题 FAQ（更详细版）

> ✅ 建议：多数问题与 API Key / Webhook / DB_PATH 配置相关，排查优先看系统设置与通知机器人配置。

### Q1. 无法登录 / 登录后空白页

- 检查 `EMBY_HOST` 是否可访问
- 确保使用 Emby 管理员账号登录
- 确认 `EMBY_API_KEY` 是否填写正确

### Q2. 数据全部为 0 或无统计

- 检查 Playback Reporting 插件是否安装并启用
- 如果是 API 模式，确认 Emby API Key 有权限
- 如果是本地模式，确认 `DB_PATH` 指向正确且容器内可访问

### Q3. 播放数据不更新

- Emby 的 Playback Reporting 插件需要有播放历史数据才能显示
- 确认有播放行为产生
- 重新触发一次播放事件后刷新页面

### Q4. 任务 / 缺集功能不可用

- 确认 Emby 版本不低于 4.7
- 检查 API Key 权限
- 如果配置了 MoviePilot，检查 Token 是否有效

### Q5. Telegram 机器人无响应

- 检查 `tg_bot_token` 是否正确
- 确认机器人已加入聊天并有发送权限
- 如果有代理，确保 `proxy_url` 可访问

### Q6. 播放统计只显示最近数据

- Playback Reporting 插件只会记录安装后的数据
- 若需历史数据，请等待新数据积累或导入历史数据库

### Q7. API 模式下数据更新慢

- API 模式依赖 Emby 插件接口，有一定延迟
- 若对实时性要求高，建议使用本地数据库模式

### Q8. 本地模式提示数据库不可访问

- 确认宿主机路径是否正确挂载到容器
- `DB_PATH` 必须写容器内路径，不是宿主机路径
- 确保数据库文件拥有读取权限

### Q9. 搜索框会自动填充管理员名字

- 这是浏览器自动填充行为
- 已在新版加入 `autocomplete="off"`，建议更新镜像

### Q10. 缺集补货后仍显示缺失

- Emby 入库有一定索引延迟
- 等待 1-3 分钟刷新页面
- 如仍不更新，可手动触发一次扫描或重启 Emby

### Q11. Telegram 推送正常但海报不显示

- 可能是图片代理地址不可访问
- 检查 `emby_public_url` 配置
- 确保外网可访问 Emby 资源封面

### Q12. 版本更新后配置丢失

- 检查是否保留了 `./config` 挂载目录
- 确认容器启动参数中的 `./config:/app/config` 是否存在

## 4. 日志查看建议

```bash
docker logs -f emby-pulse
```

出现报错时，将日志截图或复制，可用于进一步排查。
