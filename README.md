# 小源 QQ 机器人

基于 NoneBot2 + DeepSeek 的 QQ 群聊机器人，支持技术答疑、记忆系统、语义搜索、天气查询等功能。

## 功能特性

- **LLM 对话** — 基于 DeepSeek API，支持自然的多轮对话
- **记忆系统** — SQLite 消息存储 + 权重衰减 + 自动摘要压缩
- **语义搜索** — ChromaDB 向量存储，检索历史相关讨论（BGE 中文嵌入模型）
- **用户画像** — 自动记录群成员发言习惯和技术方向
- **天气查询** — 支持"冷不冷"等自然语言查询 + 每日定时推送（wttr.in）
- **静默窗口** — 聚合连续消息再回复，模拟真人聊天节奏
- **特殊模式** — 夸夸、点草（友好吐槽）、嵌入式冷笑话
- **视觉识别** — 支持图片描述（需配置 Vision API）
- **自动冒泡** — 长时间冷场自动卖萌
- **复读检测** — 群聊复读梗自动跟上
- **戳一戳回复** — 被戳会随机回复卖萌语录

## 环境要求

| 依赖 | 说明 |
|------|------|
| Windows 10+ | QQ 桌面端仅支持 Windows（LLBot 注入方式） |
| Python 3.10+ | 机器人运行环境 |
| QQ 账号 | 机器人的 QQ 号（建议用小号） |
| DeepSeek API Key | LLM 对话（[platform.deepseek.com](https://platform.deepseek.com)） |
| LLBot 或 NapCatQQ | OneBot v11 协议桥接，连接 QQ 与机器人 |

## 快速开始

### 1. 克隆项目

```bash
git clone <repo-url> xiaomo-bot
cd xiaomo-bot
```

### 2. 安装依赖

```bash
pip install -e .
```

首次启动时会自动下载嵌入模型 `BAAI/bge-small-zh-v1.5`（约 100MB）。

### 3. 配置

```bash
# 复制环境变量模板
copy .env.example .env

# 编辑 .env，填入你的 API Key
notepad .env
```

必填配置项：

```ini
DEEPSEEK_API_KEY=sk-xxxxx      # DeepSeek API Key（必填）
ONEBOT_WS_URL=ws://127.0.0.1:3001  # OneBot 连接地址
```

编辑 `config.yaml`，修改：

```yaml
allowed_group_ids: ["你的群号"]   # 允许回复的群
bot:
  qq_id: "机器人QQ号"
weather:
  target_group: "天气推送群号"    # 不需要可删除整个 weather 块
```

### 4. 自定义人设

```bash
copy data\persona.example.md data\persona.md
notepad data\persona.md
```

可选：创建群聊记忆库：

```bash
copy data\memory.example.md data\memory.md
notepad data\memory.md
```

### 5. 安装 QQ 桥接（二选一）

QQ 桥接是机器人收发消息的通道。将 QQ 桌面端的消息转发给 NoneBot，同时把机器人的回复发回 QQ。

**方案 A：LLBot（推荐，最简单）**

1. 下载 [LLBot](https://github.com/LLOneBot/LuckyLilliaBot/releases) 或访问官网 [luckylillia.com](https://luckylillia.com)
2. 解压到任意目录（如 `C:\LLBot`）
3. 编辑 `config.json`，确认反向 WebSocket 配置指向机器人：

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

4. 运行 `llbot.exe` — 它会自动检测本地 QQ 并注入桥接 DLL
5. 手机扫码登录 QQ

**方案 B：NapCatQQ**

1. 下载 [NapCatQQ](https://github.com/NapNeko/NapCatQQ/releases)
2. 配置 WebSocket 连接到 `ws://127.0.0.1:8080/onebot/v11/ws`
3. 启动 NapCatQQ 并登录 QQ

> **注意**：QQ 桌面端必须保持运行。LLBot/NapCatQQ 需要注入到 QQ 进程中，仅支持 Windows。

### 6. 启动

```bash
# 方式一：直接运行
python bot.py

# 方式二：使用启动脚本（会同时检测并启动 LLBot）
start_bot.bat
```

## 项目结构

```
xiaomo-bot/
├── bot.py                  # 主入口
├── config.yaml             # 配置文件（群号、LLM参数等）
├── pyproject.toml          # Python 包配置
├── start_bot.bat           # 启动脚本 (Windows)
├── start_bot.ps1           # 启动脚本 (PowerShell)
├── src/plugins/xiaomo/     # 插件代码
│   ├── __init__.py         # 插件入口 & 生命周期
│   ├── persona.py          # 人设加载 & system prompt 构建
│   ├── llm.py              # LLM 客户端 (DeepSeek / OpenAI 兼容)
│   ├── handlers.py         # 消息处理器（核心逻辑）
│   ├── memory.py           # 记忆系统（上下文构建 & 压缩）
│   ├── database.py         # SQLite 数据库层
│   ├── vector_store.py     # 向量语义搜索 (ChromaDB)
│   ├── window.py           # 静默窗口管理器
│   ├── auto_action.py      # 自动冒泡、复读检测
│   ├── weather.py          # 天气查询 & 定时推送
│   ├── vision.py           # 图片识别
│   ├── config.py           # 配置加载
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
allowed_group_ids: ["123456789"]

# Bot 基本信息
bot:
  name: "小源"
  qq_id: "机器人QQ号"

# LLM 配置
llm:
  provider: "deepseek"
  api_base: "https://api.deepseek.com/v1"
  api_key: "${DEEPSEEK_API_KEY}"
  model: "deepseek-chat"
  max_tokens: 4096
  temperature: 0.8

# 记忆系统
memory:
  weight_half_life_minutes: 60    # 记忆权重半衰期
  max_context_tokens: 8000        # 上下文最大 token
  compress_threshold_tokens: 15000 # 触发压缩的阈值
  keep_recent_messages: 50        # 始终保留最近 N 条

# 向量语义搜索
vector:
  model: "BAAI/bge-small-zh-v1.5" # 嵌入模型
  search_results: 10

# 静默窗口（收到消息后等待N秒再回复）
silent_window:
  private_seconds: 3
  group_seconds: 5

# 自动行为
auto_action:
  bubble_inactive_minutes: 30    # 多少分钟无消息触发冒泡
  bubble_cooldown_minutes: 60    # 冒泡冷却
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
| `DEEPSEEK_API_KEY` | DeepSeek API Key | — |
| `DEEPSEEK_API_BASE` | API 地址 | `https://api.deepseek.com/v1` |
| `DEEPSEEK_MODEL` | 模型名 | `deepseek-chat` |
| `VISION_API_KEY` | 视觉识别 API Key | — |
| `VISION_API_BASE` | 视觉 API 地址 | — |
| `VISION_MODEL` | 视觉模型 | `mimo-v2.5` |
| `DATABASE_PATH` | 数据库路径 | `data/xiaomo.db` |
| `HF_ENDPOINT` | HuggingFace 镜像 | `https://hf-mirror.com` |
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

通常是 DeepSeek API 超时或额度耗尽。检查 API Key 和余额。

## License

MIT
