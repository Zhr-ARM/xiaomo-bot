# 小源维护日志

## 2026-08-03

维护目标：过一遍群聊参与链路，继续提升“像真人一样自然水群”的能力。

本次改动：
- 新增短期群聊文本流缓存 `state.group_recent_texts`，记录最近几句群聊作为运行时语境，不写入长期数据库，避免污染长期记忆。
- 主动接话前的 AI gate 现在会收到最近群聊流，能判断“这句插话接得上接不上”，减少突兀冒头。
- 主动接话真正进入 LLM 回复时，也会附带 `[RECENT_GROUP_FLOW]`，让回复能贴着刚刚几句话走。
- `proactive_join` 增加 `recent_context_messages` 配置，默认取最近 8 条实时群聊文本。
- 对不同接话动作增加硬长度预算：`react` 这类轻反应会按 `join_max_chars` 继续压短，避免轻插话变成小作文。
- 补充测试覆盖短期群聊流记录/过期、主动接话上下文块、接话预算、默认配置。

验证：
- `python -m compileall -q src/plugins/xiaomo`
- `python -m pytest -q`
- 结果：`69 passed`

## 2026-08-03

维护目标：让机器人进入 `1070638552` 群，并把非 @ 的自然水群参与度调高一些。

本次改动：
- `allowed_group_ids` 加入 `1070638552`，现在 `1056259135` 和 `1070638552` 都会被群聊事件处理。
- 将 `proactive_join` 冷却从 360 秒降到 150 秒，回复窗口从 1.2 秒降到 0.9 秒，提高接话时效性。
- 提高主动接话概率：`react=0.38`、`short_reply=0.68`、`helpful_reply=0.88`。
- 在 `interaction.py` 增加社交开口识别，如“有人”“聊聊”“水群”“有无”等，更容易自然接上闲聊。
- 降低普通疑问、情绪开口、观点开口的接话门槛，同时保留忙群、机器人刚连续发言后的硬刹车。
- 更新 README 中群白名单和主动接话配置说明。
- 新增配置默认值测试，并补充社交开口/轻情绪接话测试。

验证：
- `python -m compileall -q src/plugins/xiaomo`
- `python -m pytest -q`
- 结果：`64 passed`

## 2026-06-18

维护目标：提高群聊机器人可靠性、上下文理解能力、工具调用透明度和回复速度。

本次改动：

- 修复静默窗口批量消息按第一条消息归因的问题。现在会选择最合理的当前回复目标，并把同一窗口内其他群友的话作为带昵称的背景。
- 修复纯文本 `@` 误触发问题。只有明确 `@` 机器人 QQ 或配置昵称时才响应。
- 搜索工具新增结构化状态：区分已搜索、未触发、未配置、无可用结果。搜索失败时会要求模型诚实说明，不再假装查到了。
- 天气查询从收消息阶段移到回复处理阶段，并与搜索并发执行，减少 @ 后的等待。
- 记忆上下文不再重复注入最近聊天，当前消息也会从历史上下文中排除，避免模型把同一句话吃两遍。
- 群聊历史上下文批量加载群友昵称，减少“用户 QQ”导致的指代理解错误。
- 语义向量检索改用当前问题作为 query，并在压缩旧记忆后同步删除对应向量，减少旧记忆污染。
- 主动戳人先经过统一互动判断和 AI gate，避免夜间或不合适场景过度活跃。

验证：

- `python -m compileall -q src/plugins/xiaomo`
- `python -m pytest -q`
- 结果：`39 passed`

后续维护约定：

- 机器人显得“不聪明”时，先查三件事：当前发言人是否正确、工具状态是否被注入、记忆上下文是否重复或污染。
- 每次改动涉及行为策略、工具调用、记忆结构、主动互动，都要补至少一个回归测试。
- 重要维护继续记录在本文件，写清楚“为什么改、改了什么、怎么验证”。

## 2026-07-07

维护目标：继续提升群聊参与的类人化程度，让机器人先理解当前语境，再决定是否回复、是否查工具，以及回复多长。

本次改动：
- 新增本地群聊理解层 `intelligence.py`，在不额外调用 LLM 的情况下判断场景、语气、回复目标和工具计划。
- 将群聊理解结果接入 `handlers.py` 主回复流程，策略层和最终 LLM 都能看到“当前应该怎么接话”的隐藏提示。
- 收紧搜索触发：入口层传来的 `search_text` 只作为候选，只有明确搜索或明显实时问题才真正调用联网搜索；天气和普通闲聊不会误触发搜索。
- 天气、搜索、图片、记忆等工具意图统一进入 `ToolPlan`，避免一个问题同时被多个旧规则抢着解释。
- 增加最终回复自检：去掉可能泄漏的系统提示前缀，按场景二次限制长度，让闲聊回复更像群友顺手接一句。
- 新增 `tests/test_social_intelligence.py`，覆盖天气优先、普通 @ 不搜索、明确搜索、实时问题搜索、闲聊短回复和提示泄漏清理。

验证：
- `python -m compileall -q src/plugins/xiaomo`
- `python -m pytest -q`
- 结果：`45 passed`
- 重启验证：NoneBot 进程已启动，uvicorn 监听 `127.0.0.1:8080`，OneBot V11 显示 `Bot 3115709797 connected`。

## 2026-07-07

维护目标：让机器人更积极地自然加入群聊，但仍然避免刷屏、抢话和夜间打扰。

本次改动：
- 在 `interaction.py` 新增主动插话机会评分器，输出 `silent`、`react`、`short_reply`、`helpful_reply` 四档动作。
- 评分综合求助意图、疑问句、情绪开口、有趣话题、群热闹程度、机器人近 5 分钟发言次数、距离上次机器人发言时间，以及机器人发言后是否已有足够人类轮次。
- 在 `handlers.py` 接入非 @ 群聊消息：高分机会先过本地评分、群级冷却、概率门和原有 AI gate，通过后才排入正常 LLM 回复队列。
- 移除旧的 5% 固定话术接话逻辑，避免脚本感和双重主动发言。
- 新增 `proactive_join` 配置：默认 10 分钟群级冷却，按动作设置触发概率，主动插话窗口为 1.5 秒。
- 新增测试覆盖高分求助、热闹/刚发言时沉默、夜间沉默、决策 payload 稳定性。

验证：
- `python -m compileall -q src/plugins/xiaomo`
- `python -m pytest -q`
- 结果：`49 passed`
- 重启验证：NoneBot PID `21272` 已启动，uvicorn 监听 `127.0.0.1:8080`，OneBot V11 显示 `Bot 3115709797 connected`。

## 2026-07-07

维护目标：让最终回复少一点“AI 答案味”，更像群友自然说话，同时不改事实内容。

本次改动：
- 新增 `tone_polisher.py`，提供发送前本地语气修整：去掉正式答题壳、压短闲聊、减少重复口癖、清理空 markdown 外壳和隐藏提示泄漏。
- 新增 `build_tone_instruction`，在 LLM 生成前提示它先接语境、少写作文式开头、不要自我介绍或强行卖萌。
- 在 `handlers.py` 最终回复链路中接入：`post_check_reply` → `shape_reply` → `polish_tone`，保留原有可靠性检查，再做语气自然化。
- 对代码块和表格做保护，避免语气修整破坏技术回答里的代码内容。
- 新增 `tests/test_tone_polisher.py`，覆盖正式开头清理、闲聊列表压短、代码块保留、口癖去重和语气提示预算。

验证：
- `python -m compileall -q src/plugins/xiaomo`
- `python -m pytest -q`
- 结果：`53 passed`
- 重启验证：NoneBot PID `30476` 已启动，uvicorn 监听 `127.0.0.1:8080`，OneBot V11 显示 `Bot 3115709797 connected`。

## 2026-07-07

维护目标：把主动插话调得更积极，同时保留防刷屏刹车。

本次改动：
- 下调主动插话动作阈值：普通疑问句更容易进入 `short_reply`，求助类更容易进入 `helpful_reply`。
- 提高基础插话分和“冷场/低消息量”加分，轻微降低中等活跃群聊和较久之前机器人发言的扣分。
- 保留强约束：夜间不主动、刚说过话强扣分、机器人 5 分钟内说太多仍强扣分、极热闹群仍降权。
- 调整 `proactive_join` 默认配置：群级冷却从 600 秒降到 360 秒，窗口从 1.5 秒调到 1.2 秒，触发概率提升为 `react=0.22`、`short_reply=0.50`、`helpful_reply=0.76`。
- 新增测试覆盖中等疑问句会进入短接话、观点开口至少能触发轻反应。

验证：
- `python -m compileall -q src/plugins/xiaomo`
- `python -m pytest -q`
- 结果：`55 passed`
- 重启验证：NoneBot PID `47180` 已启动，uvicorn 监听 `127.0.0.1:8080`，OneBot V11 显示 `Bot 3115709797 connected`。

## 2026-07-20

维护目标：支持 Linux 端启动水群功能，并修复开机自启动不稳定的问题。

本次改动：
- 新增 `start_bot.sh`，Linux/macOS 可直接启动 NoneBot；会创建日志目录、检查 `.env`/人设文件、等待 8080 就绪，并在找到 Linux LLBot 可执行文件时自动拉起桥接。
- 新增 `scripts/install_linux_autostart.sh`，安装 systemd user service：`xiaomo-bot.service`，支持 `--now` 立即启动、`--no-llbot` 外部桥接模式、`--disable` 移除服务。
- 新增 `scripts/install_windows_autostart.ps1`，用 Startup 快捷方式安装 Windows 开机自启动，比直接放 bat/vbs 更稳。
- 修复 `start_bot.vbs`：改为绝对路径加引号启动 `start_bot.bat`，避免开机启动时工作目录不对或中文路径被 cmd 误解析。
- 修复 `start_bot.bat`：增加 `-NoProfile`，并显式使用脚本目录。
- 修复 `start_bot.ps1`：不再全局杀掉所有 `node`/`llbot`/`pmhq`；只清理本项目相关进程。若 8080 被无关进程占用，会报错退出而不是误杀。
- 移除自启动场景下的 `Read-Host` 交互等待；缺 `.env` 或依赖失败时直接写日志并退出，避免开机后台卡住。
- 更新 `README.md`，补充 Linux 启动、Linux systemd 自启动、Windows 自启动安装/移除命令。
- 新增 `tests/test_startup_scripts.py`，覆盖 Linux/Windows 启动资产和关键防回退规则。

验证：
- `python -m compileall -q src/plugins/xiaomo`
- `python -m pytest -q`
- 结果：`60 passed`
- PowerShell 静态语法检查：`start_bot.ps1`、`scripts/install_windows_autostart.ps1` 均通过。
- Linux bash 实机语法检查未在本机完成：当前 Windows 环境的 WSL 虚拟化不可用，因此用文本级脚本测试兜底。
- 当前 Windows 用户自启动已更新：`C:\Users\zhr\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\Xiaomo QQ Bot.lnk` 指向新的 `start_bot.vbs`。
