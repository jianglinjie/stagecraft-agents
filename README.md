# stagecraft-agents

用 Python 复现多 Agent 内容生产平台的核心机制：dispatch-and-return、带状态机的共享 Plan Store、可恢复的 turn 与人工介入、带回放的 SSE、turn 租约、记忆压缩、MCP，以及一个 LangGraph 对照实现。

业务刻意做成通用的「brief → outline → draft → render」，工具全部是假实现，所以这个仓库讲的是机制，不是内容。

## 里程碑

| # | 里程碑 | 状态 |
|---|--------|------|
| 1 | 工具注册表 + 单 Agent | 完成 |
| 2 | 四角色 + dispatch-and-return + Plan Store | 完成 |
| 3 | 可恢复的 turn、人工介入、SSE、租约 | 完成 |
| 4 | 记忆、压缩、资产池 | 完成 |
| 5 | MCP server/client + LangGraph 对照 | 完成 |
| 6 | 评测集 | 框架与基线完成；提示词前后对比待重跑（见第 6 节） |
| 7 | 混合检索（BM25 + 向量 + RRF） | 完成；向量一路待接入 embeddings 端点（见第 7 节） |

## 运行

```bash
uv sync
cp .env.example .env      # 任何 OpenAI 兼容端点都可以
uv run pytest             # 测试用脚本化的假模型，不联网
uv run --env-file .env python -m stagecraft.agents.single "Turn https://example.com/p/1 into a short article."
uv run --env-file .env python -m stagecraft.agents.single --chat "Fetch https://example.com/p/1"   # 多轮
uv run --env-file .env python -m stagecraft.agents.orchestrator "Write a two-part series about https://example.com/p/1, review the plan first."
```

评测（真实模型，报告默认写到 `.data/evals/`）：

```bash
uv run --env-file .env python evals/run.py --category routing                # 只跑一类
uv run --env-file .env python evals/run.py --label baseline --repeat 3 \
  --prompt orchestrator=evals/prompts/orchestrator-v1.md --judge-model deepseek-v4-pro
uv run python evals/run.py compare .data/evals/baseline.json .data/evals/prompt-v2.json
```

HTTP 服务（数据默认写到 `.data/`）：

```bash
uv run --env-file .env python -m stagecraft.api          # http://127.0.0.1:8000
curl -s -X POST localhost:8000/sessions                    # 拿到 session id
curl -N localhost:8000/sessions/<id>/events                # 另开一个终端看 SSE
curl -s -X POST localhost:8000/sessions/<id>/messages \
  -H 'content-type: application/json' \
  -d '{"content": "Write one short article about https://example.com/p/1", "client_message_id": "m1"}'
```

stderr 打印每次工具调用和返回（`->` / `<-`），stdout 是最终回复。`--chat` 之后可以继续追问，历史由 `follow_up()` 带到下一轮。

测试控制台（Vite + React + shadcn/ui，见下方「控制台」一节）：

```bash
uv run python -m stagecraft.api --demo       # 离线演示规则，不需要 key，数据在 .data/demo/
# 或 uv run --env-file .env python -m stagecraft.api   # 真实模型
cd web && pnpm install && pnpm dev           # http://localhost:5173，/api 代理到 :8000
```

## 目录

```text
src/stagecraft/
  agents/    orchestrator / router / planner / executor；roles.py 角色工具表；runtime.py；
             dispatch.py 三个 dispatch 工具；submit.py；single.py 单 Agent；fake_model.py
  plan/      model.py、state_machine.py、store.py（SQLite + 版本号 CAS + 角色守卫）、errors.py
  tools/     registry.py；results.py；context.py（注入的调用方角色）；fake/ 内容工具；plan/ 计划工具
  api/       app.py 路由；turns.py 后台 turn；events.py 事件总线；leases.py 租约；sessions.py 消息与 turn
  memory/    turn_context.py；session_memory.py；items.py 历史修复；compaction.py；long_term.py
  assets/    model.py；store.py（来源身份、归档记录、统一出口）；errors.py
  db.py      可嵌套组合的 SQLite 事务
  mcp/       server.py 把产品暴露成 MCP 服务器；client.py 白名单接入外部 MCP 工具
  graph/     同一流程的 LangGraph 版：workflow.py 图与节点；agents.py 节点里的模型调用
  evals/     cases.py 用例模型；trace.py 录制；checks.py 结构化断言；judge.py；runner.py；report.py；cli.py
  tools/retrieval.py  ReferenceIndex：BM25 + 向量 + RRF，search_references 工具
  agents/demo_model.py  离线演示规则（--demo），api/console.py 控制台的只读接口
web/         测试控制台：Vite + React + TypeScript + Tailwind + shadcn/ui
evals/       run.py 入口；cases/*.yaml 52 条用例；prompts/ 被评测的提示词版本
docs/        framework-comparison.md 两种编排方式的对照；retrieval-notes.md 普通 RAG 与 GraphRAG；
             corpus/ planner 检索的 12 篇规范
  config.py  模型端点配置（OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL）
tests/
```

---

## 控制台：在浏览器里测这些机制

`web/` 是一个开发者控制台，用来手动测前面各里程碑的机制，而不是产品界面。

**页面。**

- **会话**（里程碑 2–4）：左边是对话，每一轮展开成工具调用卡片；dispatch 卡片下面挂着子 agent 的 `sub_run`，能看到它收到的 payload（它的全部输入）和它调用的工具。右边五个标签：
  - **Plan**：只由 `state` 事件重建，显示每个 stage 的状态、contract（含 sources 指针，点开跳到检索页）和 runtime；
  - **事件**：原始 SSE 帧，带 seq；可以断开、按 `after=本地 seq` 续传、从任意 seq 回放（重复帧标出并忽略）、清空从头回放；
  - **资产**：活跃与已归档资产，勾选后随下一条消息从面板归档；
  - **记忆**：Turn Context 原文、会话记忆条数和估算 token（对照压缩阈值）、按主题的长期档案和“重写档案”按钮；
  - **请求**：控制台发出的每个写请求，对照 202 started、202 duplicate、409、422。
- **输入框**：示例消息、附件（含“同来源再登记”“同名不同源”两个预设）、归档名（填不存在的名字测 422）、可编辑的 `client_message_id` 和“原样重发”（测幂等），turn 运行中再发一条测 409。
- **检索**（里程碑 7）：直接调用索引，显示模式、指针、RRF 分数和命中的检索路。
- **工具**（里程碑 1）：注册表里每个工具由签名生成的 schema，以及角色 × 工具矩阵。
- **评测**（里程碑 6）：读取 `.data/evals/` 和 `docs/evals/` 里的结果 JSON，显示分类通过率、不稳定用例、失败详情，以及两次运行的对比。

**为此加的后端接口。** `GET /sessions` 列出会话；`/console/*` 是只读接口（info、tools、references、会话的 Turn Context 与记忆、评测结果），不写数据、不调模型，也不替任何角色调工具。SSE 多了一种事件 `sub_run`：子 agent 的运行不是流式的，dispatch 返回时把 payload 和子 agent 的工具调用作为一个事件发出，紧挨在这次 dispatch 的 `item_completed` 之前。它先记在本轮状态里，等 dispatch 的输出进入事件流再发；如果在子 agent 结束时直接发，可能抢在还排在流里的 `item_started` 前面。

**离线演示模式。** `python -m stagecraft.api --demo` 让四个角色都跑 `DemoModel`：按角色写死的规则，只看真实模型能看到的东西（带 Turn Context 的 instructions、payload、本轮工具结果），按关键词和工具结果里的 `next_action` 决定下一步。它能把路由、提问中断、评审闸门、重试后 blocked、引用 sources、归档都走一遍，所以没有 key 也能测前后端的机制；但它不代表模型能力，评测和实测仍然要真实端点。`STAGECRAFT_DEMO_DELAY` 控制每步停顿（默认 0.4 秒，方便看流式和测 409），`STAGECRAFT_MEMORY_THRESHOLD_TOKENS` 调低后能在页面上触发压缩。

**取舍。**

- 前端通过 Vite 代理访问 API，后端不用配 CORS；`STAGECRAFT_API` 可指向别的地址。
- 续传不用浏览器 EventSource 自带的重连：它会重复 URL 里原来的 `after`。控制台自己记位置，每次重连带最新的 seq；服务端 `last_seq` 比本地小，说明服务端重启、内存日志清空了，就从头订阅。
- 事件 reducer 和评测汇总是纯函数，有 Vitest 单测；评测的通过率、不稳定用例、翻转用例的算法和 `report.py` 保持一致。

**测试覆盖。** 后端：会话列表按时间倒序；`sub_run` 事件带 payload、调用和输出，并严格位于 dispatch 的开始与完成之间；演示规则经 HTTP 跑出带 sources 的评审计划；console 各接口的字段、校验和 404；评测目录里不是结果的 JSON 被忽略，label 不能越出目录。演示规则：直接路径的语气和格式、提问、只说“批准”时再问一次、每次批准才执行、跳过上游导致 blocked、聊天里归档、压缩器和档案重写。前端：事件重复帧只应用一次、resync、失败、子运行挂到正确的 dispatch、快照与事件流合并、评测汇总与对比。另用无头 Chrome 走过一遍完整流程：新建会话、附件、提问与回答、评审与批准、409、422、原样重发、事件回放、记忆重写、检索、工具、评测报告与对比。



**解决什么问题。** 一个工具有三样东西：给模型看的说明、运行时的参数校验、真正干活的函数。分开手写，它们会慢慢不一致，模型按过期的说明传参，校验按新规则拒绝，没人知道错在哪。还有两个常见的坑：模型传错参数时工具直接抛异常，整轮就崩，模型没有机会自我纠正；工具把抓到的原文、写好的全文整段回传，几轮之后上下文就被撑满。

**怎么设计。**

- `@tool` 装饰器读函数签名：类型、默认值、`Annotated` 里的描述，加上 docstring 第一段，用 pydantic `create_model` 生成一个参数模型（`src/stagecraft/tools/registry.py`）。
- 这一个模型两处用：导出成 JSON schema 是模型看到的说明；调用前 `model_validate` 是运行时校验。
- `ToolSpec.invoke` 永远返回一个结果模型，不向模型抛异常。参数错返回 `ToolError(code="invalid_arguments", issues=[...], hint=...)`，执行失败返回 `ToolError(code="tool_failed")`。错误是数据，模型读到后自己决定改参数重试、换工具，还是告诉用户。
- 结果只放决策需要的东西：id、状态、一句摘要、下一步建议。原始产物留在 `FakeWorkspace`（内存里的「数据库 + 对象存储」），后面的工具凭 id 取。
- `ToolRegistry` 按名注册、同名报错、按名挑选。`select()` 把 `ToolSpec` 适配成 SDK 的 `FunctionTool`，Agent 只挑名字，不装配工具。
- `FakeModel` 实现 SDK 的 `Model` 接口，按脚本一轮轮返回工具调用或最终回复，并记录每次收到的输入。测试因此能跑真正的 `Runner` 循环和真正的工具执行，不联网，还能断言「模型在第二轮看到了第一轮的工具结果」。

**schema 双用的意义。** 一份定义两处消费，说明和校验不会漂移。模型看到的 `enum`、`minimum`、`required` 和运行时拒绝的条件是同一条规则；改一个字段，说明、校验、类型提示同时更新；`extra="forbid"` 让多传的字段也被当成错误报回去，而不是被静默丢掉。

**取舍。** 默认不开 OpenAI 的 strict 模式，很多兼容端点不支持，靠运行时校验兜底，需要时 `select(strict=True)` 打开。参数描述必须写在 `Annotated` 里，docstring 只取第一段，不解析 Args 段落，换取实现简单和没有歧义。

**测试覆盖。** schema 从签名生成、缺注解或缺 docstring 被拒、同名注册报错、参数错和执行异常都变成结构化错误、异步工具被 await、四个假工具串成一条流水线且结果不含原文、单 Agent 用假模型跑通「调两个工具后回复」、出错后一次纠正、一轮里并行两个工具调用。

---

## 里程碑 2：四角色 + dispatch-and-return + Plan Store

**解决什么问题。** 单 Agent 做长任务有三个毛病。一是上下文越跑越长，抓到的原文、写好的草稿全堆在一起，又贵又容易被带偏。二是进度只存在对话里，进程一重启就不知道做到哪了。三是「用户确认后再干活」只是提示词里的一句话，模型可能自作主张跳过。

**怎么设计。**

- **四个角色。** orchestrator 面向用户；router 判断 direct 还是 workflow；planner 只写 stage 契约；executor 一次只执行一个 stage。每个角色能用哪些工具写在 `agents/roles.py` 一张表里，角色只挑名字，不装配工具。
- **dispatch-and-return。** 三个 `dispatch_*` 工具各开一次全新的 `Runner.run`，子 Agent 的全部输入就是 payload（plan_id、stage_id、goal），跑完把一个小结果还给 orchestrator。
- **Plan Store 是唯一介质。** planner 和 executor 从不直接通信。一个 stage 分两半：contract 由 planner 写，说明要产出什么；runtime 由 executor 写，记录实际产出的 id。
- **状态机管进度。** pending 到 doing 之间**没有边**，必须先进 `waiting_user(plan_review)`，而离开 waiting_user 必须带 `user_confirmed=true`。确认闸门不是模型要记住的规则，而是图里缺的一条边。另外三条规则：没有 contract 不能开始，没有产出不能 done，终态不接受任何迁移。
- **角色守卫在 store 里。** planner 只能写 contract，executor 只能写 runtime，状态迁移只允许 orchestrator。角色来自 `RunContext`，由启动这次运行的代码注入，不出现在任何工具 schema 里，模型看不到也伪造不了。
- **版本号 CAS。** 整个 plan 存成一行 JSON，写入是 `UPDATE ... WHERE id = ? AND revision = ?`，受影响行数为 0 就是有人抢先写了。调用方带 `expected_revision` 时还会先比对。
- **信 store，不信汇报。** 子 Agent 跑完后 dispatch 重新读 store，汇报实际写进去的 stage 和 ref。planner 说写了三个其实只写了一个，orchestrator 看到的就是一个。
- **结果通过 `submit_*` 工具交回。** SDK 的 `output_type` 在 Chat Completions 上会发 `response_format: json_schema`，DeepSeek 直接返回 400。function calling 所有目标端点都支持：提交参数不合法会作为错误还给模型修正，子 Agent 用文字收尾而不提交会被报告成 dispatch 失败。这是接真实端点时才发现的问题。

**dispatch-and-return 与 handoff 的区别。** handoff 把整段对话交给下一个 Agent，由它接管用户，原 Agent 退场。dispatch 是把子 Agent 当成一个工具调用：orchestrator 发出任务、等结果、继续掌控对话。多阶段生产需要一个始终在场的协调者来推进状态、拦截确认，所以用 dispatch。

**为什么共享状态而不是消息传递。** 如果 planner 把计划作为消息发给 executor，计划就只存在于某次对话里：重启会丢，无法校验，也说不清谁改过什么。放进 Plan Store 后，计划可以持久化、有版本号、按角色限制写入，任何实例、任何一轮都读得到同一份。executor 失败重跑时，contract 还在，已完成的 runtime ref 也还在，只重做 `pending_items`。

**上下文隔离的收益。** 子 Agent 看不到父对话，只看到 payload 和它自己用工具读到的指针。成本上，executor 不用为前面几十轮对话付 token。质量上，它不会被用户闲聊或上一个 stage 的草稿内容带偏，也没机会把对话里出现过的 id 当成自己的产出。测试里用一个只出现在用户消息中的标记字符串，断言它从未进入任何子 Agent 的输入。

**取舍。**

- `expected_revision` 是可选的。并发 authoring 靠它防覆盖；状态推进另有状态机兜底，done 是终态，重放的「标记完成」不会生效两次。真实系统里 replan 这类破坏性操作应该强制带版本号。
- 每个 stage 都必须经过 plan_review，交互轮次多一些，换来闸门无法绕过。
- 整个 plan 存成一行 JSON，每次写都重写整份文档，但 CAS 一条 SQL 就够；stage 数量上去之后再拆表。

**实测（DeepSeek）。** 第一轮：router 判为 workflow，建 plan，planner 写出三个 stage，orchestrator 把第一个置为 plan_review 后停下等确认。第二轮用户批准：带 `user_confirmed=true` 进入 doing；executor 第一次只完成了一个工作项，orchestrator 按 `next_action` 带 `retry_ids` 重试；全部完成后标记 done，并把下一个 stage 置为 plan_review 再次停下。模型自己带上了 `expected_revision`。这次实测也暴露出 planner 会规划工具做不到的工作项，所以现在 planner 的提示词末尾会从注册表生成一份 executor 能力清单。

**测试覆盖。** 状态转移表 36 种组合逐一断言；确认、契约、产出三条规则；版本冲突与两线程并发只有一个成功；另一个连接插入写入时 SQL 层 CAS 生效；九种越权写入被拒；重开数据库文件后计划仍在。端到端：四个角色各用一个脚本化假模型跑完「路由、建计划、写两个 stage、评审、确认、执行、完成」两轮对话；子 Agent 只看到 payload；direct 路径不建计划；汇报以 store 为准；未确认时 executor 不会被调用；没有产出无法 done；子 Agent 拿不到也借不到 dispatch 工具；子 Agent 崩溃变成工具错误；非法提交可修正；用文字收尾算失败；planner 能看到能力清单。

---

## 里程碑 3：可恢复的 turn、人工介入、HTTP 与 SSE

**解决什么问题。** 前两个里程碑只能在一个进程里跑一轮。真实产品还要回答五个问题：几分钟的 Agent 运行怎么不卡住请求；planner 问完问题，下一轮怎么接上；浏览器断线重连，怎么补上漏掉的进度；网络重发怎么不跑两遍；同一个会话怎么不被两个请求同时跑。

**怎么设计。**

- **持久化和运行分离。** 发消息的接口只做必须成功的事：幂等检查、拿租约、在一个事务里写入用户消息和一条 running 的 turn，然后立刻返回 202。Agent 在后台任务里跑，进度走 SSE。请求返回时消息已经落库，后台就算崩了，turn 行也会记下它是怎么结束的。
- **planner 按 task_id 恢复。** dispatch_planner 给 planner 挂一个 SDK 的 `SQLiteSession`，key 是 task_id，默认和 plan 绑定。同一个任务第二次 dispatch，planner 看到的是上次的完整对话加上新 payload。session 存在文件里，重启后照样接得上。
- **提问即中断，由代码保证。** planner 提交的 questions 非空时，dispatch 把问题写进 plan，并把第一个已写好的 pending stage 置为 `waiting_user(plan_review)`。orchestrator 的 `tool_use_behavior` 一看到工具结果带 `interrupt: true` 就结束本轮，模型不会再被调用，也就不可能接着派发 executor。用户回答后带上 answers 再 dispatch，问题清空，planner 在同一个 session 里改写 stage。
- **事件总线 = 日志 + 广播。** 每个会话一个有上限的日志，加上一组在线订阅者队列。分配 seq、追加日志、推给订阅者在同一个临界区里完成，对应 Redis 版本里 Lua 脚本做的 INCR、XADD、PUBLISH。订阅顺序是先注册、再回放、再接实时：回放期间新发布的事件既进日志也进队列，按 seq 去重，所以既不丢也不重。
- **断线重连。** SSE 每帧带 `id: seq`，浏览器重连时自动带上 `Last-Event-ID`，服务端只回放比它新的事件。如果客户端落后太多、日志已经裁掉了中间部分，就发一个 `resync` 事件让它去拉快照，而不是假装没有缺口。
- **state 事件是完整快照。** turn 开始时、每次 plan 或 dispatch 工具返回后、turn 结束时，都推一份完整的 plan。前端面板只靠它重建，不解析 Agent 说的话。
- **消息 id 幂等。** `client_message_id` 在会话内唯一。检查放在拿租约之前：重发一条已存储的消息，即使它的 turn 还在跑，也返回同一个 turn_id 并标记 duplicate，而不是 409。数据库唯一约束兜底并发重发。
- **turn 租约。** SQLite 一条 upsert 实现 `SET NX PX`：没有记录就插入，已过期就覆盖，未过期时 `rowcount` 为 0，返回 409。后台定时续租，续租和释放都要求 token 匹配。进程死了续租就停，租约到期自动可用。续租被拒说明别人已经接管，本轮立即取消并发出 `turn_failed(lease_lost)`，不和新持有者并排写。

**事件协议。** `turn_started`、`item_started`、`item_delta`、`item_completed`、`turn_completed`、`turn_failed`、`state`，重连时可能多一个 `resync`。工具调用和文本消息都是 item，用 `kind` 区分。

**为什么持久化和运行分离。** 如果在请求里跑完 Agent 再返回，几分钟的运行会占住连接，网关超时后客户端不知道消息到底有没有收到，只能重发，于是跑两遍。先落库再返回，客户端拿到 202 就确定消息已存在；重发靠 `client_message_id` 识别；进度和结果从事件流和快照接口拿，和那次 HTTP 请求的生死无关。

**取舍。**

- 事件总线是进程内实现，接口按 Redis Streams + Pub/Sub 的形状设计。多实例部署时替换实现，订阅端逻辑不用改。
- 幂等检查和拿租约不是一个原子操作。同一条消息的两个并发重发，一个拿到租约，另一个在 409 分支里再查一次消息，查到就当重复处理。极端时序下仍可能返回 409，客户端重试即可。
- 中断没有用 SDK 的 `needs_approval` 和 RunState，因为问题要持久化在 plan 上，跨请求、跨重启都存在，而不是挂在某次运行的内存状态里。
- 接 SSE 时发现 SDK 的流式运行会用 `dataclasses.asdict` 给模型对象算指纹，会深拷贝模型的所有字段，所以脚本化假模型改成了普通类。

**实测（DeepSeek）。** 起服务后发一条消息立刻得到 202；用同一个 client_message_id 重发返回 duplicate；再发另一条返回 409。SSE 里依次看到 dispatch_router 判为 direct、四个内容工具各自的开始和完成事件、177 个文本 delta、turn_completed。结束后快照显示一轮 completed、一问一答两条消息。

**测试覆盖。** 事件总线：seq 按会话递增；先回放尾部再接实时；Last-Event-ID 只补缺口；回放期间发布的事件恰好到达一次且有序；落后太多触发 resync；空闲心跳；跨线程发布保序。租约：同时只有一个持有者；续租要 token；持有者死亡后到期可接管，旧持有者醒来续租和释放都失败且不影响新持有者。恢复与中断：同一任务的第二次 dispatch 接着上次对话；重启后照样接上；提问后本轮只调用两次模型、executor 未被调用、stage 进入 plan_review；回答后问题清空、stage 被改写。HTTP（真实 uvicorn）：事件顺序和 seq 连续、state 快照、文本 delta 拼起来等于最终回复；Last-Event-ID 重连无缺口无重复；重发幂等；运行中再发 409、结束后可发；同一条消息在自己运行时重发不算冲突；失败的 turn 发 turn_failed 并释放会话；404 和 422。TurnService：租约被接管时本轮以 lease_lost 结束且不动新持有者；长 turn 期间续租让租约一直有效。

---

## 里程碑 4：记忆、压缩、资产池

**解决什么问题。** 会话要跨刷新和重启续上；长会话的 token 会超限；工具改名或进程被杀，会让恢复出来的历史直接把下一轮打挂；用户想弃用一张图，但计划还在引用它；同一个页面反复抓取，产生一堆近似重复的资产；多次会话积累的偏好需要沉淀下来，又不能互相覆盖。

**怎么设计。**

- **三层记忆，按寿命由短到长。**
  - **Turn Context**：每次调用模型前现场组装，放在 instructions 里。SDK 的 instructions 可以是函数，每次调用模型时执行。内容是当前计划摘要、活跃资产的指针、这条消息上传和归档了什么以及影响哪些 stage、这个主题的长期档案。因为在 instructions 里，SDK 不会把它写进会话历史：不占下一轮的 token，也不会在历史里过期。同一轮里 planner 改了计划，下一次调用模型看到的就是新摘要。
  - **会话记忆**：`SessionMemory` 实现 SDK 的 Session 接口，存 SQLite，每轮结束写回，重启后恢复。orchestrator 按会话一份，planner 按任务一份。
  - **长期记忆**：每个主题一份档案，会话结束后由模型整体重写。
- **读历史时修复。** 两种情况会让恢复出的历史把下一轮打挂：工具改名后，历史里有调用不存在工具的记录；进程被杀后，有工具调用没有结果，Chat Completions 会直接返回 400。读取时把这两种调用折叠成一条 assistant 说明，保留参数和结果，并丢掉找不到调用的孤儿结果。这是视图不是迁移：库里原样保存，工具改回原名后历史照常可用。
- **压缩。** 估算的 token 超过阈值时，把较早的部分交给模型写成摘要，替换成一条 assistant 消息。切点永远落在用户消息上，最近几轮原样保留，工具调用和它的结果不会被拆开。删除旧行和插入摘要在一个事务里完成。摘要模型失败就什么都不改，本轮照常用完整历史跑，下一轮再试。
- **长期记忆用重写而不是追加。** 追加会无限增长，还会把「喜欢正式」和「改成活泼」并排留着。重写时把当前档案和会话记录一起交给模型，返回完整的新档案：还成立的保留，新学到的加入，被推翻的删掉，并有长度上限。两个会话同时结束时按 revision 做 CAS，输的一方重新读取赢家的档案再重写，两边学到的都保留。
- **资产以来源为身份。** 活跃资产里同一个 `source_id` 只有一条，再次登记只更新摘要，不新增。名字在活跃资产里唯一，重名自动加序号。两条规则都由 SQLite 的部分唯一索引保证。
- **归档是记录，不是删除，也不可恢复。** 归档写入时间、原因（用户在聊天里要求、被新产出替代、从资产面板归档）和触发它的消息。数据库触发器拒绝清空归档时间，也拒绝删除资产行，所以「不可恢复」不只是应用层的约定。用户想要回同样的素材，就作为新资产重新登记。
- **统一出口。** 所有按名字取资产的地方都走 `AssetStore.resolve`。已归档的统一返回 `asset_archived`，找不到的返回 `not_found` 并列出可用的名字。`asset_get` 和 `write_draft(reference_assets=...)` 收到的拒绝一字不差。
- **归档回报依赖。** 归档会返回仍在使用这些资产的未完成 stage，依据是 contract 里的 `assets` 字段。模型调用 `archive_session_assets` 时如果有依赖，会先被拒绝并返回 `assets_in_use`，要求先告诉用户、重新规划或拿到确认，再带 `force=true` 调用。用户从面板归档的资产随消息到达，Turn Context 会写明哪些 stage 受影响。
- **一次写入。** 用户消息、它的 turn、这条消息的上传和归档在同一个事务里落库。`Database` 让事务可以嵌套组合，只有最外层真正执行 BEGIN 和 COMMIT。归档列表里有一个不存在的资产，整条消息都不会落库，接口返回 422，会话也不会被占住。

**为什么 Turn Context 只放指针和本轮变化。** 放全文会让每次调用都为同样的内容付费，还会让模型去「读」而不是去「查」。指针加上按需调用工具，上下文大小就和资产数量、文档长度脱钩了。只放本轮变化，是因为之前的变化已经在会话记忆里；当前状态（计划、活跃资产）每次都从存储重建，所以永远是最新的。

**工具改名后旧历史怎么办。** 读取时按当前工具名修复，不改存储。调用了已不存在工具的记录变成一条说明，模型知道发生过什么，但不会再去调用那个工具。被中断的调用同样变成说明，并提示先用工具核对状态。

**取舍。**

- token 用字符数除以 4 估算，粗糙但与供应商无关，只用来决定什么时候压缩。
- 压缩在本轮开始前同步进行，会给这一轮多一次模型调用的延迟，换来本轮就能用上压缩后的历史。
- 长期记忆的提取目前是一个显式接口（`POST /sessions/{id}/memory`），还没有在会话结束时自动触发。

**实测（DeepSeek）。** 第一轮随消息上传 hero 和 logo 两张图，模型写草稿时通过统一出口引用了两者。第二轮用户说「以后别用 logo」，模型以 `user_request` 归档 logo，再只用 hero 重写草稿。三轮之后对存储的历史做压缩：29 条、约 3369 token 变成 16 条、约 2385 token，摘要保留了 brief、outline、draft 的 id 和用户的决定。对这个会话提取长期记忆，得到的档案包括读者是开发者、偏好活泼语气、logo 已被拒绝不要再用。

**测试覆盖。** 历史修复：正常历史不变；已退役工具的调用折叠成保留参数和结果的说明；被中断的调用变成说明；孤儿结果被丢弃；长内容截断。会话记忆：重启后恢复；pop 与 clear；读取时修复但存储不变，工具改回原名后历史复原；切点落在用户消息上并保留最近几轮；压缩替换旧轮次；低于阈值不压缩；摘要失败时什么都不变且下次成功；压缩后跑新一轮时模型先看到摘要。资产：同来源只更新；重名加序号；归档是记录且之后无法使用；重复归档只报告；数据库拒绝恢复和删除，再次登记变成新资产；统一出口列出所有问题；归档回报未完成的依赖 stage；本轮变更要么全部落库、要么都不落。长期记忆：整体重写不追加；有长度上限；并发重写合并而不覆盖。Agent 层：两个按名取资产的工具拒绝方式完全一致；有依赖时归档被拒、强制后才归档并记录触发消息；Turn Context 出现在每次模型调用中、计划变化后被重建、从不进入会话记忆；主题档案出现在 Turn Context 里。HTTP：上传和面板归档随消息一起落库，并出现在下一轮的 Turn Context 中；资产变更被拒时消息、turn、资产都不落库，且可以用同一个消息 id 重试；从会话提取长期记忆。

---

## 里程碑 5：MCP 与 LangGraph 对照

**解决什么问题。** 两件事。一是边界：产品要能被 IDE 和其他 agent 调用，也要能用外部 MCP 服务器提供的工具，但两个方向都不能绕过产品自己的规则。二是框架选择：把同一个多角色流程用 Agents SDK 的「模型编排」和 LangGraph 的「图编排」各写一遍，才能讲清两者的差别，而不是背概念。

**怎么设计。**

- **MCP 服务器只暴露用户层操作。** `create_session`、`send_message`、`get_plan`、`get_stage_detail` 四个工具，与 HTTP 接口一一对应。外部 agent 是产品的用户，不是子 agent：它不能直接改 stage 状态、写契约或调用内容工具，只能发消息，所以确认闸门、租约、消息幂等对它同样生效。`send_message` 默认等待本轮结束，并把每次工具调用转成 MCP progress 通知，相当于把 SSE 事件流换成请求/响应协议能承载的形态。传输走 stdio，日志只写 stderr。
- **MCP 客户端默认拒绝。** 外部服务器配置在 `STAGECRAFT_MCP_SERVERS`（JSON 列表），每个服务器必须显式列出 `allowed_tools`，写 `["*"]` 才是全部放行。放行的工具以 `<server>__<tool>` 注册进同一个注册表，角色还要在 `extra_role_tools` 里点名才能持有。schema 原样展示给模型，参数校验交给服务器；服务器报错变成 `tool_failed` 结果。header 和 env 里的 `${VAR}` 从环境变量展开，密钥不写进配置本身。
- **LangGraph 版。** `src/stagecraft/graph/` 用 `StateGraph` 重写同一流程：route 之后分 direct 和 plan；plan 有问题时进入 ask（interrupt）；随后 present_review 把 stage 置为待评审，await_review 用 interrupt 等决定，驳回回到 plan 改写，批准进入 execute；execute 之后重试、进入下一个评审或结束。计划存在图状态里，由 checkpointer 按 thread_id 存档；换一个 SQLite checkpointer 实例照样从停下的节点接着跑。所有状态变化仍然经过同一个 `check_transition`。
- **对照文档。** [docs/framework-comparison.md](docs/framework-comparison.md) 从控制流、状态位置、中断与恢复、可观测性、MCP 配合五个维度比较，每段附取舍。

**MCP 在这里的边界。** 对外，暴露「用户能做的事」，不暴露内部工具；对内，外部工具默认拒绝、逐个放行、角色点名才能用。两个方向的规则都和编排框架无关。

**在 Claude Code 或 Cursor 里使用。** 路径换成本机的绝对路径。

```bash
claude mcp add stagecraft -- uv run --directory /abs/path/stagecraft-agents \
  --env-file /abs/path/stagecraft-agents/.env python -m stagecraft.mcp
```

Cursor 的 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "stagecraft": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/stagecraft-agents",
               "--env-file", "/abs/path/stagecraft-agents/.env",
               "python", "-m", "stagecraft.mcp"]
    }
  }
}
```

**取舍。**

- MCP 的 `send_message` 同步等待一轮，长任务可能超过客户端超时。这时传 `wait=false` 立即返回，再用 `get_plan` 查看进度。
- 外部工具的参数不在本地校验，错误要等服务器返回。
- 图版的 agent 不读写 Plan Store，节点直接把状态切片交给它们，所以图版没有「executor 只拿指针、自己读契约」这一层。

**实测（DeepSeek）。** 用 MCP 客户端通过 stdio 启动服务器：发一条消息后依次收到 dispatch_router、plan_create、dispatch_planner、plan_update_stage_state 四条 progress 通知，本轮停在 plan_review；`get_plan` 返回四个 stage 和下一步动作，`get_stage_detail` 返回第一个 stage 的契约。图版跑同一类请求：planner 写出四个 stage，图在每个 stage 执行前用 interrupt 停下，逐个批准后四个 stage 依次完成。

**测试覆盖。** MCP 服务器（内存客户端）：只有四个用户层工具，没有内部工具，`ctx` 不在 schema 里；发消息会等待本轮，并把工具调用按顺序转成 progress；结果带计划摘要和下一步；同一个消息 id 重发返回 duplicate；`get_plan` 与 `get_stage_detail` 正常；未知会话、运行中再发、没有计划时都返回工具错误。MCP 客户端：从环境变量读取配置并展开密钥；缺变量、传输方式冲突、缺白名单、格式错误都被拒绝；只放行白名单里的工具，schema 原样、结构化结果、服务器错误变成 `tool_failed`；角色不点名就拿不到外部工具，orchestrator 点名后能调用。LangGraph：direct 路径一个节点就结束；提问、回答、评审、部分完成后重试、驳回后改写、批准、完成的完整流程，检查每次中断载荷、checkpoint 里的状态、节点交给 agent 的 payload；关掉 SQLite checkpointer 后重新打开并恢复；驳回时 executor 从未被调用，两次都没有产出时 stage 变为 blocked。

---

## 里程碑 6：评测集

**解决什么问题。** 改了提示词、换了模型、调了工具说明，怎么知道是变好还是变坏？手工试几条对话，只能看到自己想得到的情况；模型输出又有随机性，同一条请求这次对、下次错。需要一套固定用例，一条命令跑完，结果能说清失败在哪、能和上一次对比。

**怎么设计。**

- **用例是数据。** `evals/cases/*.yaml` 共 51 条（里程碑 7 又加了 1 条，现为 52 条），分四类：路由 15 条（direct 还是 workflow）、工具调用 12 条（调了什么、参数对不对、有没有多做）、任务完成 10 条（最终 stage 状态和产物）、鲁棒性 14 条（提示词注入、越权写 contract、跳过评审、信息不足应提问）。每条是一段脚本化对话，加上某一轮之后或全部结束后必须成立的条件。工作流用例用「自动批准」模拟用户：只要有 stage 在等评审就回一句批准，直到没有可批的、出现需要回答的问题、某一轮毫无进展，或者到达轮数上限。
- **结构化断言优先。** 断言读事实，不读措辞：Plan Store 里每个 stage 的状态，工作区产出了什么（数量、格式、语气），每个角色调了哪些工具、按什么顺序、参数是什么（先补上工具默认值再比较），router 选了哪条路。只有「是否提问」「是否提到某个 id」这几项看回复文本。每条用例默认还查一次 id 是否真实：回复和工具参数里出现的 brief、outline、draft、render、plan、stage id 必须是系统发过的，用户自己打的 id 除外。
- **加载时校验名字。** `excludes: [plan_write_contract]` 这种拼错的工具名永远通过，所以用例里的工具名必须是某个角色真实持有的，否则加载直接报错。
- **judge 只评结构管不到的部分。** 固定 rubric 四项：是否回应了请求、下一步是否清楚、对进度的描述是否与系统状态一致、是否简洁。每项 1 到 5 分，带锚点描述；最低分不小于 3、均分不小于 4 才算过。judge 能看到计划和产物的真实状态，所以「说草稿写好了其实没有」会在 honest_status 上被扣分。只有结构化断言全部通过的用例才送 judge。judge 也走 OPENAI_BASE_URL，用 submit 工具交分；`--judge-model` 换一个和被测模型不同的型号，实测用 deepseek-v4-pro 评 deepseek-flash。rubric 带版本号，写进报告。
- **失败和错误分开。** 端点超时、限流、5xx 记为 error，不算 failed。每个角色的模型外面包一层 `MeteredModel`，即使子 agent 的端点失败被 dispatch 工具转成了 tool_failed、orchestrator 接着往下跑，也知道这次测量不可信。error 自动重跑一次，通过率不计 error。
- **被测提示词是输入。** `--prompt orchestrator=path` 替换角色的基础提示词。报告头记录每个角色提示词的 sha256 前 12 位和来源；评测过的版本存进 `evals/prompts/`，随时能重跑。代码里的 orchestrator 提示词必须是其中已记录的某一版，否则测试失败，提醒先存版本、比较过再上线。
- **重复运行，分出噪声。** `--repeat 3` 每条跑三次，按运行次数算通过率，并列出时过时不过的用例。
- **报告。** markdown 写分类通过率、judge 均分、不稳定用例，以及每次失败的检查项、看到的事实、每轮每个角色的工具调用序列、orchestrator 发给子 agent 的内容和回复摘录。同时写一份 JSON，`evals/run.py compare a.json b.json` 输出分类变化和翻转的用例。
- **会中止的错误。** 401、402、403 说明账户被拒（密钥错、余额不足、无权限），之后每个请求都会同样失败。遇到这种错误，整次运行立即停止：不再启动新用例，不写报告，退出码 3。同名报告已存在时默认拒绝覆盖，除非显式传 `--overwrite`。这两条都来自下面的实测事故。

**实测（DeepSeek，被测 deepseek-flash，judge 用 deepseek-v4-pro）。**

1. **第一次跑，先修用例。** 50 条各跑一次，45 条通过。逐条看失败，有两条是用例测错了东西。一条工具调用用例检查「brief 是否从用户给的 URL 抓取」，但 router 把请求判成了 workflow，第一轮还没执行，失败的其实是路由。另一条完成类用例没给读者对象，planner 提问恰恰是它的提示词要求的行为。修正方式：不依赖路由的工具调用用例改为跑完自动批准后，再检查整条用例；缺受众的用例补上受众；路由问题补一条路由用例，单独衡量。只修测量本身，不为了让 agent 通过而放宽期望。
2. **正式基线。** 当前的 orchestrator 提示词记为 `evals/prompts/orchestrator-v1.md`。51 条各跑 3 次，共 153 次，136 次通过（89%）：路由 93%、工具调用 94%、完成 80%、鲁棒性 86%。7 条用例三次结果不一致；24 次 judge 评分全部通过。这次运行共 2036 次模型请求、940 万输入 token、50 万输出 token，并发 10，耗时 6.4 分钟。
3. **按角色归因。** 17 次失败的分布：
   - **planner 8 次。**
     - 5 次：用户说「先给我看计划」，planner 把审批当成自己的事，要么在 questions 里问「你批准这个计划吗」，要么写出 executor 做不了的「确认大纲」工作项，导致 stage blocked。
     - 2 次：planner 让一份大纲拆出两篇草稿，但 `write_draft` 只能按整份大纲写。
     - 1 次：信息齐全仍追问篇幅和语言。
   - **orchestrator 4 次。**
     - 3 次：「写一篇关于我们新产品的文章」没有问是哪个产品，直接按字面生成。
     - 1 次：拒绝把没有产出的 stage 标为 done 时，只解释原因，没有问用户下一步。
   - **router 3 次。** 「抓 brief 再写大纲」被判成 workflow。
   - **用例 2 次。** 落地页用例同样缺少受众，基线之后补上了。

   另外，orchestrator 在 `dispatch_executor` 之后经常沿用 dispatch 之前读到的 revision，先撞一次 `revision_conflict`，重读后才写成功，白花一次调用。
4. **候选提示词 v2**（`evals/prompts/orchestrator-v2.md`）只改基线里出现过的问题：
   - 请求里没有可抓取的对象时先问，不猜；
   - 交给 planner 的 goal 只写产出什么（交付物、受众、语气、格式），不写用户想怎么评审；
   - dispatch 之后第一次写入不带旧的 revision；
   - 做不到时说明原因并让用户选下一步。

   第二条针对的是 planner 的失败，但 planner 只看得到 payload，「先给我看计划」只可能经由 orchestrator 写的 goal 传过去，所以根子在 orchestrator。router 和 planner 自己的问题不在这次改动范围内，预计仍会失败，留作下一轮。
5. **中断，对比待补。** 为了在报告里记录 orchestrator 发给子 agent 的内容，用修正后的用例重跑基线时，跑到一半 DeepSeek 账户余额耗尽，返回 HTTP 402。当时的运行器把 402 当成 agent 失败继续跑，通过率掉到 61%。这份结果不可信，已删除；而它用了同一个 label，正式基线的报告文件也被覆盖了，所以上面的数字暂时没有对应的报告文件。上面「会中止的错误」那一条（账户被拒即中止、默认不覆盖）就是为此加的。充值后按下面三步补齐：两份报告放进 `docs/evals/`，v2 胜出才替换代码里的提示词，然后在这里写结论。

```bash
uv run --env-file .env python evals/run.py --label baseline --out docs/evals --repeat 3 \
  --prompt orchestrator=evals/prompts/orchestrator-v1.md --judge-model deepseek-v4-pro
uv run --env-file .env python evals/run.py --label prompt-v2 --out docs/evals --repeat 3 \
  --prompt orchestrator=evals/prompts/orchestrator-v2.md --judge-model deepseek-v4-pro
uv run python evals/run.py compare docs/evals/baseline.json docs/evals/prompt-v2.json
```

**取舍。**

- 结构化断言比让 judge 打分难写，但它不漂移、不花钱，失败时说得出看到了什么。judge 只评沟通质量。
- judge 和被测模型同属一家，仍可能偏好同类文风。换型号、让 judge 看系统状态、固定 rubric 和锚点，能减轻但消除不了。基线里 24 次评分全部通过，说明这份 rubric 的区分度还不够，下一步要用写坏的回复给 judge 做负例校准。
- 51 条用例各跑 3 次，总通过率差几个百分点仍可能是噪声，基线里就有 7 条用例三次结果不一致。判断一次改动，主要看哪些用例翻转、哪类失败消失，而不只看总分。
- 用例在看到基线之后修正过，只修测量问题。但没有留出验证集，候选提示词是看着开发集写的；用例多了以后，应该把开发集和验证集分开。
- 工具是假的，所以评测只衡量编排：路由、工具调用、状态推进、闸门和提问，不衡量内容本身的质量。

**测试覆盖。** 用例集：40 到 60 条、四类齐全、id 唯一。加载时报错的情况：工具名拼错、角色不持有该工具、未知匹配算子、跨文件重复 id。匹配算子；代码里的 orchestrator 提示词必须是已记录的版本。执行：direct 用例在脚本化 agent 下通过，参数按默认值补全后比较；失败的检查项写出实际看到的事实；编造的 id 被抓到，用户给的 id 豁免；自动批准把工作流推进到 done；某一轮崩溃算失败，且不再发送后续轮次。错误分类：端点故障算 error 并重跑一次，包括子 agent 内部被 dispatch 工具吞掉的故障；账户被拒（402）时立即中止、不写报告、不再启动新用例，orchestrator 和子 agent 两种情况都测。judge：只在结构化断言通过后运行，按固定阈值判定，看得到系统状态；不交分算 error。报告：分类表、失败详情和发给子 agent 的内容；compare 的分类变化和翻转用例。CLI：离线运行，提示词替换生效并记录指纹，未知用例 id 报错，同名报告默认拒绝覆盖。

---

## 里程碑 7：混合检索

**解决什么问题。** planner 写 contract 时，「面向开发者怎么写」「PDF 规格表放什么」只能靠模型自己的常识。团队已有的受众、语气、格式规范写在文档里，模型看不到；把整份规范塞进提示词，又贵又会稀释注意力。需要按需检索，并让 contract 记下它依据了哪几条规范，评审和执行时都能追溯。

**怎么设计。**

- **语料与切块。** `docs/corpus/` 下 12 篇规范，按 `##` 小节切成 48 块。指针是 `<文件名>#<小节 slug>`，比如 `tone-playful#headlines`。只要文件不改名、小节不改标题，指针就稳定。
- **两路召回。** BM25（`rank_bm25`）按词匹配，擅长「PDF」「captions」这类精确词，但匹配不到同义改写。向量走 OpenAI 兼容的 `/embeddings`，能用「刚入门的读者」找到 beginners 规范，但对精确词和数字不敏感。两路各取前 20 个候选。
- **RRF 融合。** BM25 分数没有上界且依赖语料，余弦相似度挤在一个窄区间里且依赖模型，两者不能直接相加，归一化又要调参，任何一边一换就失效。RRF 只看名次：`score = Σ 1/(60 + rank)`。两路都排在前面的文档，胜过只在一路排第一的文档。同分时依次比较命中路数、最好名次和指针，顺序是确定的。
- **只回指针和摘要。** `search_references` 的每条结果只有指针、标题、首句摘要（最多 160 字符）、融合分数和命中的检索路，不含正文。和其他工具一样，结果只放下一步决策需要的东西。
- **指针写进 contract 的 sources。** `plan_write_stage_contract` 新增 `sources` 参数，写入前逐个检查指针是否在索引里；编造的指针返回 `not_found`，提示照 `search_references` 返回的原样使用。信索引，不信作者的记忆。planner 提示词加了一条：stage 依赖受众、语气、格式或系列约定时先检索，把用到的指针写进 sources。检索工具只给 planner；executor 通过 `plan_get_stage_detail` 看到 contract 里的指针。
- **启动时建索引，降级要说出来。** HTTP 服务和 MCP 服务在 lifespan 里建索引，和之后处理检索请求的是同一个事件循环；命令行在启动时建。embeddings 端点可以单独配置（`EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY`、`EMBEDDING_MODEL`），默认沿用聊天端点。端点不可用时记一条告警、只用 BM25，每次结果的 `mode` 都写明 `bm25_only`；单次查询的向量失败只降级那一次。
- **RAG 与 GraphRAG。** 见 [docs/retrieval-notes.md](docs/retrieval-notes.md)。
- **评测也用上语料。** 真实模型的评测运行会加载同一份语料，报告头写明语料规模和检索模式。新增一条用例：依赖受众、语气和格式约定的计划，planner 要先检索，再把指针写进 contract。

**为什么只回指针。** 这和其他工具的规则一样：planner 要决定的是「这个 stage 依据哪几条规范」，不需要读完正文。正文进上下文要花 token，还会让模型去复述规范，而不是引用规范。指针写进 contract 以后，评审时看得到依据。目前还没有按指针读取正文的工具，executor 只在 `plan_get_stage_detail` 的结果里看到这些指针。

**取舍。**

- 摘要用小节首句，不调模型生成。好处是确定、零成本；前提是语料每节都以主题句开头，所以写语料要守这条规矩。
- 分词只做英文小写和停用词，不做词干还原，也不做中文分词。「render」和「rendering」在 BM25 里是两个词，这正是向量那一路要补的。
- BM25 的候选按「是否和查询有共同词」筛，再按分数排序，不用「分数大于 0」。Okapi 的 IDF 对出现在一半以上小节里的词是零或负数，语料小的时候，真正匹配的小节会被过滤掉；这是写测试时发现的。
- 向量召回没有相似度下限，无关的查询也会返回 top-k，是否采用由 planner 看摘要判断。语料只有几十块，直接点积就够了，不需要向量数据库。
- 索引在内存里，启动时全量构建，改了语料要重启才生效。embedding 没有缓存，语料变大以后应该按内容哈希缓存。
- 图版（LangGraph）的 planner 没有接检索。

**实测。** DeepSeek 没有 embeddings 接口，`/v1/embeddings` 返回 404。用仓库里的 `.env` 启动索引：48 个小节，记一条告警后进入 BM25-only 模式，每次结果都标明 `bm25_only`。「playful tone headlines for a developer audience」「PDF datasheet specification table」「captions and length for a short video」这几类查询，排第一的分别是对应规范的对应小节。「people new to the topic」这种改写式查询，BM25 找不到 beginners 规范，排第一的是因为「people」一词命中的管理者规范，这正是向量那一路要解决的问题。向量召回和两路融合只在测试里用假 embedder 验证过；配置一个提供 embeddings 的端点（`EMBEDDING_BASE_URL` 等）即可启用混合模式。planner 调用检索、把指针写进 contract 的端到端流程，在测试里用脚本化模型跑通；真实模型的验证和里程碑 6 的对比一样，要等账户充值后再跑，命令是 `uv run --env-file .env python evals/run.py --case tools-planner-cites-references --repeat 3`。

**测试覆盖。**

- **RRF：** 按名次倒数求和的顺序与手算一致；两路都靠前的胜过单路第一；k 决定第一名的权重；同分时顺序确定。
- **切块：** 指针稳定，标题正确，摘要取首句并截断。
- **检索：** 结果只含指针、标题、摘要、分数和命中路，正文里的句子不会出现在工具结果里。用假 embedder 时，向量召回到 BM25 找不到的小节，两路都命中的排在前面。embeddings 失败时降级为 BM25 并告警。仓库自带的语料能回答格式和语气问题。
- **配置：** embeddings 配置默认沿用聊天端点，可单独指定，也可以关掉。
- **contract：** 索引里的指针能写进 sources；编造的指针被拒，计划不变；没有配置语料时，任何 sources 都被拒。
- **端到端：** planner 先检索，再把指针写进 contract；executor 不持有检索工具。
- **评测：** sources 检查读的是 Plan Store 里的 contract。
