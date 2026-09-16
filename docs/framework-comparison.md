# 同一拓扑的两种写法：OpenAI Agents SDK 与 LangGraph

仓库里同一个四角色流程写了两遍：`src/stagecraft/agents/` 用 OpenAI Agents SDK，`src/stagecraft/graph/` 用 LangGraph。两边共用 Plan 和 Stage 模型、状态机 `check_transition`、假内容工具、`submit_*` 提交方式和脚本化假模型，差别只在编排层。下面按五个维度比较，每段先说两边怎么做，再说取舍。

## 1. 控制流由谁掌握

**Agents SDK 版**：下一步做什么由 orchestrator 模型决定。它读工具结果里的 `next_action`，自己选择调用 `dispatch_planner`、`plan_update_stage_state` 还是直接回复。代码不规定顺序，只在关键处设闸门：状态机里没有 pending 到 doing 的边；`dispatch_executor` 要求 stage 已经处于 doing；planner 提问时，`stop_on_interrupt` 在代码里直接结束本轮。

**LangGraph 版**：控制流是图的边。route 之后走 direct 还是 plan，评审之后回 plan 还是去 execute，执行之后重试、进入下一个评审还是结束，全部由条件边上的普通函数决定。模型只在节点内部做它擅长的事：判断路由、写计划、调用内容工具。

**取舍**：模型编排灵活。用户中途改主意、插一句题外话、要求跳过某一步，orchestrator 都能接住，不需要为每种情况画一条边；代价是行为不完全可预测，要靠代码闸门、`next_action` 和评测兜住。图编排可预测、好测，每条路径都能单独断言；代价是每种对话分支都得显式建模，用户说「第二部分先放着，把第一部分改成 PDF」，图里没有对应的边就接不住。流程固定、合规要求高的场景适合图；对话形态开放、需要随机应变的场景适合模型编排，同时把不可妥协的规则下沉到代码闸门里。

## 2. 状态放在哪里

**Agents SDK 版**：计划在 Plan Store（SQLite，一行 JSON 加 revision），会话历史在 `SessionMemory`，资产在 `AssetStore`。agent 通过工具读写状态，写入按角色守卫。状态和运行是分开的：任何进程、任何一轮、HTTP 快照接口、MCP 的 `get_plan` 读到的都是同一份。

**LangGraph 版**：计划就在图状态里，由 checkpointer 按 `thread_id` 存档。节点读取状态、返回增量，由 reducer 合并（`log` 字段用 `operator.add` 追加）。agent 不碰状态：节点把需要的那部分放进 payload，再把 agent 提交的结果写回。

**取舍**：图状态省掉了一整层存储、工具和角色守卫，状态变化也天然留有历史，每个 checkpoint 都能回看。代价是状态被锁在「某个图的某个线程」里：别的服务想读计划，要么通过图去取 checkpoint，要么再同步一份出去；而 checkpoint 的形状跟着图结构走，改了节点可能影响还没跑完的旧线程。独立的 Plan Store 多写了不少代码，但它是产品级的领域对象，前端面板、MCP、审计都直接用，并发写入也有 revision CAS 保护；checkpointer 则默认同一线程不会被并发推进。

## 3. 中断与恢复

**Agents SDK 版**：等待用户不是运行时的暂停点，而是持久化的业务状态。stage 进入 `waiting_user(plan_review)`，问题写进 `plan.open_questions`，本轮正常结束。下一条用户消息开启新的一轮，模型从 Turn Context 和 Plan Store 看到该继续什么。planner 的对话按 task_id 存在会话记忆里，再次 dispatch 就接着上次。进程崩溃靠租约过期和持久化状态恢复，不存在「恢复一个执行到一半的函数」。

**LangGraph 版**：`interrupt()` 让图停在节点中，checkpoint 记下停在哪个节点、中断载荷是什么。`Command(resume=...)` 把用户的决定作为 `interrupt()` 的返回值送回，从那个节点继续。测试里关掉 SQLite checkpointer、重新打开、再 resume，图从 `await_review` 接着跑。

**取舍**：LangGraph 的中断是框架原语，写起来直观，停在哪、带着什么在等，一目了然。要注意恢复时节点会从头重新执行，`interrupt()` 之前的代码必须幂等，所以图里把「把 stage 置为待评审」和「等待评审结果」拆成了两个节点，前者的状态变化先被 checkpoint 记下。SDK 版没有暂停原语，一开始就把等待建模成数据，好处是和请求生命周期天然解耦，Web、MCP、另一台实例都能看到「在等什么」并推进它；代价是恢复逻辑分散在状态机、Turn Context 和提示词里，需要自己保证没有遗漏。

## 4. 可观测性

**Agents SDK 版**：SDK 自带 tracing，每次模型调用、工具调用、子 agent 运行都是一个 span（本仓库默认关闭，避免没有配置时向外发送）。产品层自建了事件协议：`item_started`、`item_delta`、`item_completed`、`state`、`turn_*` 经事件总线推送到 SSE，带 seq 可以回放；MCP 服务器把工具调用转成 progress 通知。模型在做什么、调了什么看得很清楚，但「流程现在走到哪一步」需要从 Plan Store 读。

**LangGraph 版**：流程位置是一等公民。`aget_state` 直接给出 `next`（下一个要执行的节点）、当前状态和挂起的中断；checkpoint 历史可以逐步回看，甚至从某个点分叉重跑。节点内部的模型和工具调用不在图的视野里，需要另外接入 tracing。

**取舍**：两边的强项正好相反。SDK 版看清模型行为，流程状态要自己做出来；图版看清流程，节点里的模型行为要额外手段。排查「为什么第二阶段没执行」，图版看一眼 `next` 和边就知道；排查「为什么模型填错了参数」，SDK 版的 trace 更直接。

## 5. 和 MCP 的配合

**Agents SDK 版**：SDK 原生支持把 MCP 服务器作为工具来源。本仓库另写了 `McpToolBridge`，把外部 MCP 工具按白名单接进同一个注册表，命名为 `<server>__<tool>`，角色还要显式点名才能持有。反方向，`build_mcp_server` 把产品暴露成 MCP 服务器，只提供用户层操作（建会话、发消息、看计划、看 stage），发消息时把事件流转成 progress 通知。

**LangGraph 版**：图本身不关心工具从哪来，MCP 工具在节点里调用即可，可以用同一个 bridge 生成的工具。把图暴露成 MCP 服务器也很自然：一个工具启动线程，一个工具 resume，一个工具读 `aget_state`，中断载荷就是要展示给调用方的内容。

**取舍**：MCP 在两边都只是边界协议，不决定编排方式。真正要想清楚的是边界放在哪一层。本仓库的选择有两条：对外暴露「用户能做的事」而不是内部工具，外部 agent 也必须发消息、经过确认闸门，无法直接调用 `plan_update_stage_state` 跳过评审；接入外部工具时默认拒绝、逐个放行，因为外部服务器随时可能增加工具。这两条和选哪个框架无关。

## 小结

| 维度 | Agents SDK 版 | LangGraph 版 |
|---|---|---|
| 控制流 | 模型决定，代码设闸门 | 图的边决定，模型在节点内工作 |
| 状态 | 独立的 Plan Store、会话记忆、资产池 | 图状态加 checkpointer，按 thread_id |
| 等待用户 | 持久化的业务状态，由下一条消息推进 | `interrupt()` 加 `Command(resume=...)` |
| 可观测 | 模型与工具 trace，自建事件协议 | `aget_state` 的 next 与 checkpoint 历史 |
| MCP | 白名单接入外部工具，对外只暴露用户操作 | 同样的边界选择，图本身无感 |

两边共用的部分恰好是最该共用的：状态机、提交协议、工具注册表、假模型。框架换了，「pending 和 doing 之间没有边」这条规则一行没改。

**实测备注（DeepSeek）**：同一类请求，图版在每个 stage 执行前用 interrupt 停下，四个 stage 逐个批准后依次完成；SDK 版通过 MCP 调用时停在第一个评审点，由下一条消息推进。图版的 executor 在第二阶段又抓取了一次 brief（workspace 里出现了 `brief_0002`），尽管节点已经给了上游指针。这类「模型绕远路」两种编排方式都会遇到，要靠提示词和评测收紧，框架本身不解决。
