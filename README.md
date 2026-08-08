# 小源 QQ 机器人

基于 NoneBot2 + MiMo-V2.5 的 QQ 群聊机器人，支持技术答疑、图片识别、记忆系统、语义搜索、天气查询等功能。小源仅支持群聊，不处理私聊消息。

## 功能特性

- **LLM 对话** — 基于 MiMo-V2.5 API，支持自然的多轮对话
- **记忆系统** — SQLite 消息存储 + 权重衰减 + 自动摘要压缩，成员记忆严格按 QQ 号隔离
- **语义搜索** — ChromaDB 向量存储，只召回当前说话人 QQ 对应的历史发言（BGE 中文嵌入模型）
- **用户画像** — 自动记录群成员发言习惯和技术方向
- **天气查询** — 自动识别提问中的城市，支持自然语言查询和每日定时推送
- **静默窗口** — 聚合连续消息再回复，模拟真人聊天节奏
- **连续对话** — 第一次 @ 或点名后可自然续聊，无需每句话重复 @；转向群友时自动收住
- **语感自适应** — 跟随近期群聊句长，抑制重复口癖、舞台动作和习惯性反问
- **特殊模式** — 夸夸、点草（友好吐槽）、嵌入式冷笑话
- **视觉识别** — 支持图片描述（需配置 Vision API）
- **上下文冒泡** — 只在存在具体未完话题时自然跟进，没有合适内容就保持安静
- **复读检测** — 群聊复读梗自动跟上
- **戳一戳回复** — 被戳会随机回复卖萌语录

## 环境要求

| 依赖 | 说明 |
|------|------|
| Windows 10+ / Linux | Windows 可直接拉起 LLBot；Linux 可运行 bot 并连接 LLBot/NapCat 等 OneBot 桥接 |
| Python 3.11+ | 机器人运行环境 |
| QQ 账号 | 机器人的 QQ 号（建议用小号） |
| MiMo-V2.5 API Key | LLM 对话 + 图片识别（api.xiaomimimo.com） |
| Tavily API Key | 可选；用于联网搜索，不配置则跳过搜索 |
| LLBot | QQ 桥接（[GitHub Releases](https://github.com/LLOneBot/LuckyLilliaBot/releases)） |

## 快速开始（4 步）

### 1. 克隆项目

```bash
git clone https://github.com/Zhr-ARM/xiaomo-bot.git
cd xiaomo-bot
```

### 2. 安装 LLBot（QQ 桥接）

从 [LLBot Releases](https://github.com/LLOneBot/LuckyLilliaBot/releases) 下载最新版，解压到 `C:\Users\<用户名>\LLBot\`（或其他路径）。

编辑 `config.json`，确认反向 WebSocket 指向机器人：

```json
{
  "ob11": {
    "enable": true,
    "connect": [{
      "type": "ws-reverse",
      "enable": true,
      "url": "ws://127.0.0.1:8080/onebot/v11/ws",
      "messageFormat": "array"
    }]
  }
}
```

### 3. 运行启动脚本

```bash
start_bot.bat
```

启动脚本会按 `constraints.txt` 同步依赖、创建缺失的 `.env` 和人设文件，再启动机器人与 LLBot。残留的 `egg-info` 不会让依赖更新被跳过。

> 如果提示缺少 Python，请先安装 [Python 3.11+](https://www.python.org/downloads/)。

### 4. 配置

编辑 `.env`，填入 MiMo-V2.5 API Key：

```ini
LLM_API_KEY=sk-xxxxx
```

编辑 `config.yaml`，修改群号和机器人 QQ：

```yaml
allowed_group_ids: ["你的群号"]
bot:
  qq_id: "机器人QQ号"
```

然后重新运行 `start_bot.bat`。

> LLBot 启动后会自动检测本地 QQ 并注入桥接，扫码登录即可。

## Linux 启动与开机自启动

Linux 端可以直接运行 Python bot，QQ 桥接可用 Linux 版 LLBot、NapCat 或其他 OneBot v11 实现。桥接端需要把反向 WebSocket 指到：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

手动启动：

```bash
chmod +x start_bot.sh scripts/install_linux_autostart.sh
./start_bot.sh
```

如果 QQ 桥接由别的服务管理，只启动水群 bot：

```bash
./start_bot.sh --no-llbot
```

`start_bot.sh` 会同时监控 Python 服务和本机 LLBot。进程存活但 QQ WebSocket 长时间断开时会重启 LLBot；退出服务时会清理由脚本启动的子进程。

安装 Linux systemd 用户自启动：

```bash
scripts/install_linux_autostart.sh --now
```

常用命令：

```bash
systemctl --user status xiaomo-bot.service
journalctl --user -u xiaomo-bot.service -f
scripts/install_linux_autostart.sh --disable
```

如果是无头服务器，希望用户未登录也能启动 systemd user service，需要额外执行一次：

```bash
sudo loginctl enable-linger "$USER"
```

Windows 开机自启动现在用快捷方式安装，避免启动目录不对：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_windows_autostart.ps1
```

移除 Windows 自启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_windows_autostart.ps1 -Remove
```

## 自定义人设

编辑 `data/persona.md` 定义机器人角色（首次运行已自动从模板创建）。

可选：复制 `data/memory.example.md` → `data/memory.md`，编辑群聊背景知识。

## 项目结构

```
xiaomo-bot/
├── bot.py                  # 主入口
├── config.yaml             # 配置文件（群号、LLM参数等）
├── pyproject.toml          # Python 包配置
├── start_bot.bat           # 启动脚本 (Windows)
├── start_bot.ps1           # 启动脚本 (PowerShell)
├── start_bot.sh            # 启动脚本 (Linux/systemd)
├── scripts/                # 自启动安装脚本
├── src/plugins/xiaomo/     # 插件代码
│   ├── __init__.py         # 插件入口 & 生命周期
│   ├── persona.py          # 人设加载 & system prompt 构建
│   ├── llm.py              # LLM 客户端 (MiMo-V2.5 / OpenAI 兼容)
│   ├── handlers.py         # 消息处理器（核心逻辑）
│   ├── dialogue.py         # 短期连续对话归属与抢话保护
│   ├── memory.py           # 记忆系统（上下文构建 & 压缩）
│   ├── database.py         # SQLite 数据库层
│   ├── vector_store.py     # 向量语义搜索 (ChromaDB)
│   ├── window.py           # 静默窗口管理器
│   ├── humanize.py         # 类人化回复策略（该不该回、怎么回、等多久）
│   ├── tone_polisher.py    # 群聊节奏感知、抗重复和最终语气整理
│   ├── auto_action.py      # 自动冒泡、复读检测
│   ├── weather.py          # 天气查询 & 定时推送
│   ├── vision.py           # 图片识别
│   ├── config.py           # 配置加载
│   ├── group_policy.py     # 群级人设、参与度与出站表达约束
│   ├── state.py            # 运行时共享状态
│   └── filter_utils.py     # 内容过滤 & 文本工具
└── data/
    ├── persona.example.md  # 人设模板 → 复制为 persona.md 自定义
    └── memory.example.md   # 记忆库模板 → 复制为 memory.md 自定义
```

## 配置参考

### config.yaml 完整选项

```yaml
# 允许回复的群号列表
allowed_group_ids: ["1056259135", "1070638552", "972277179"]

# Bot 基本信息
bot:
  name: "小源"
  qq_id: "机器人QQ号"

# LLM 配置 (MiMo-V2.5)
llm:
  provider: "mimo"
  api_base: "https://api.xiaomimimo.com/v1"
  api_key: "${LLM_API_KEY}"
  model: "mimo-v2.5"
  max_tokens: 4096
  temperature: 0.8

# 记忆系统
memory:
  weight_half_life_minutes: 60    # 记忆权重半衰期
  max_context_tokens: 8000        # 上下文最大 token
  compress_threshold_tokens: 15000 # 触发压缩的阈值
  keep_recent_messages: 50        # 始终保留最近 N 条

# 向量语义搜索（后台初始化，不阻塞机器人上线）
vector:
  model: "BAAI/bge-small-zh-v1.5" # 嵌入模型
  search_results: 10

# 静默窗口（收到消息后等待N秒再回复）
silent_window:
  private_seconds: 3
  group_seconds: 5
  explicit_group_seconds: 0.8

# 第一次 @ 后的自然连续对话
conversation_followup:
  enabled: true
  timeout_seconds: 240
  pending_seconds: 15
  window_seconds: 0.9
  max_intervening_human_messages: 2
  post_check:
    enabled: true
    stale_seconds: 45
    cancel_after_human_messages: 2

# QQ 桥接发送确认超时（超时后不自动重发，避免重复消息）
delivery:
  send_timeout_seconds: 12

# 类人化回复策略（显式 @ 或叫小源后的正式回复前置判断）
humanize:
  enabled: true
  strategy_llm_for_explicit: false
  strategy_llm_for_proactive: false # 正式回复模型一次完成主动接话判断和生成
  explicit_max_delay_seconds: 0.3
  proactive_strategy_max_delay_seconds: 0.4
  proactive_fallback_max_delay_seconds: 0.2
  strategy_timeout_seconds: 12
  max_extra_delay_seconds: 5

# 主动发言（非 @、非叫小源的自动消息）
proactive:
  ai_gate_enabled: true
  ai_gate_timeout_seconds: 20
  quiet_hours:
    enabled: true
    start_hour: 23
    end_hour: 7

# 主动接话（非 @ 时自然加入聊天）
proactive_join:
  enabled: true
  min_cooldown_seconds: 90
  window_seconds: 0.9
  recent_context_messages: 8
  local_reactions_enabled: true
  post_check:
    enabled: true
    stale_seconds: 55
    cancel_after_human_messages: 8
    cancel_if_bot_spoke: true
  probability:
    react: 0.55
    short_reply: 0.82
    helpful_reply: 0.95

# 群级覆盖：只调整指定群。明确 @ / 点名回复不受主动消息上限影响。
group_policies:
  "972277179":
    self_reference: "小源"       # 自指“我”在出站前统一为“小源”
    civil_language:
      enabled: true
      fallback: "这句容易伤人，小源不评价人，还是聊事情本身吧。"
    recruitment:
      enabled: true
      website: "https://cdut-osa.cn"
      append_on_relevant_topic: true # 招新相关话题顺势补官网，1 小时内不重复
    proactive_join:
      min_cooldown_seconds: 60
      score_bonus: 20
      max_bot_messages_5m: 3     # 所有非点名主动行为共享的硬上限
      min_human_turns_after_bot: 2
      probability:
        react: 0.72
        short_reply: 0.92
        helpful_reply: 0.98

# 发送节奏（模拟真人打字，显式 @ 会保持快）
human_timing:
  enabled: true
  chars_per_second: 22
  jitter_seconds: 0.45
  min_seconds: 0.15
  max_seconds: 3.0
  explicit_max_seconds: 0.8
  proactive_max_seconds: 1.2

# 全员自动戳会占用 AI 判断并干扰文字接话，默认关闭；指定成员和话题戳仍可用
poke_everyone_cooldown_minutes: 0

# 自动行为
auto_action:
  bubble_inactive_minutes: 30    # 多少分钟无消息后检查是否有具体未完话题
  bubble_max_inactive_minutes: 180 # 沉寂太久后不再凭空冒泡
  bubble_cooldown_minutes: 60    # 冒泡冷却
  bubble_attempt_cooldown_minutes: 15 # AI 拒绝后多久再尝试
  repeat_threshold: 3            # 复读触发阈值
  repeat_cooldown_minutes: 30    # 复读冷却

# 消息长度限制
output:
  max_chars_per_message: 800
  max_code_lines: 50

# 关键词反应（regex-free 触发器）
reactions:
  cooldown_minutes: 0
  triggers:
    "你好": "你好呀同学！(=^･ω･^=)"
    "谢": "不客气喵～"
```

### 环境变量 (.env)

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | MiMo-V2.5 API Key (文本 + 视觉) | — |
| `LLM_API_BASE` | LLM API 地址 | `https://api.xiaomimimo.com/v1` |
| `LLM_MODEL` | LLM 模型名 | `mimo-v2.5` |
| `VISION_API_KEY` | 视觉识别 API Key | — |
| `VISION_API_BASE` | 视觉 API 地址 | `https://api.xiaomimimo.com/v1` |
| `VISION_MODEL` | 视觉模型 | `mimo-v2.5` |
| `TAVILY_API_KEY` | 联网搜索 API Key（可选） | — |
| `DATABASE_PATH` | 数据库路径 | `data/xiaomo.db` |
| `HF_ENDPOINT` | HuggingFace 镜像 | `https://hf-mirror.com` |
| `HOST` | NoneBot 监听地址 | `127.0.0.1` |
| `PORT` | NoneBot 监听端口 | `8080` |
| `ONEBOT_WS_URL` | OneBot WS 地址 | `ws://127.0.0.1:3001` |

## 自定义人设

编辑 `data/persona.md` 来定义机器人的角色。文件格式为 Markdown，内容会被直接注入 system prompt。

示例结构：
```markdown
你是<名字>，<身份描述>

## 身份
- ...

## 聊天风格
- ...

## 回复规则
- ...
```

参考 `data/persona.example.md` 查看完整示例。

## 自定义记忆库

编辑 `data/memory.md` 来定义机器人知道的群聊背景知识（成员信息、黑话、群规等）。文件内容会被附加在 system prompt 末尾。

## 常见问题

### 启动后连不上 QQ

1. 确认 LLBot/NapCatQQ 已正确安装并登录 QQ
2. 确认 OneBot WebSocket 地址一致（正向或反向）
3. 查看控制台是否有连接日志

服务健康状态可直接查看：

```text
GET /healthz  # Python 服务是否存活
GET /readyz   # QQ 桥接是否已经连接
```

两个接口还会返回 `semantic_memory.status`（`initializing`、`ready` 或 `degraded`），可用于判断语义记忆是否完成加载；语义记忆降级不会阻断基础群聊。

### 首次启动下载模型很慢

设置 HuggingFace 镜像（国内用户）：

```bash
set HF_ENDPOINT=https://hf-mirror.com
```

或写入 `.env`：
```ini
HF_ENDPOINT=https://hf-mirror.com
```

### 如何更换 LLM

支持任何 OpenAI 兼容 API。在 `config.yaml` 中修改：

```yaml
llm:
  api_base: "https://your-api.com/v1"
  api_key: "${YOUR_API_KEY}"
  model: "your-model"
```

### 机器人回复"小源卡住了"

通常是 MiMo API 超时或额度耗尽。检查 API Key 和余额。

## License

MIT
