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
| 5 | MCP server/client + LangGraph 对照 | 计划中 |

## 运行

```bash
uv sync
cp .env.example .env      # 任何 OpenAI 兼容端点都可以
uv run pytest             # 测试用脚本化的假模型，不联网
uv run --env-file .env python -m stagecraft.agents.single "Turn https://example.com/p/1 into a short article."
uv run --env-file .env python -m stagecraft.agents.single --chat "Fetch https://example.com/p/1"   # 多轮
uv run --env-file .env python -m stagecraft.agents.orchestrator "Write a two-part series about https://example.com/p/1, review the plan first."
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
  config.py  模型端点配置（OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL）
tests/
```

---

## 里程碑 1：工具注册表 + 单 Agent

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

