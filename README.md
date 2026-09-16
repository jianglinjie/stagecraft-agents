# stagecraft-agents

用 Python 复现多 Agent 内容生产平台的核心机制：dispatch-and-return、带状态机的共享 Plan Store、可恢复的 turn 与人工介入、带回放的 SSE、turn 租约、记忆压缩、MCP，以及一个 LangGraph 对照实现。

业务刻意做成通用的「brief → outline → draft → render」，工具全部是假实现，所以这个仓库讲的是机制，不是内容。

## 里程碑

| # | 里程碑 | 状态 |
|---|--------|------|
| 1 | 工具注册表 + 单 Agent | 完成 |
| 2 | 四角色 + dispatch-and-return + Plan Store | 计划中 |
| 3 | 可恢复的 turn、人工介入、SSE、租约 | 计划中 |
| 4 | 记忆、压缩、资产池 | 计划中 |
| 5 | MCP server/client + LangGraph 对照 | 计划中 |

## 运行

```bash
uv sync
cp .env.example .env      # 任何 OpenAI 兼容端点都可以
uv run pytest             # 测试用脚本化的假模型，不联网
uv run --env-file .env python -m stagecraft.agents.single "Turn https://example.com/p/1 into a short article."
```

## 目录

```text
src/stagecraft/
  agents/    single.py 单 Agent；fake_model.py 脚本化假模型
  tools/     registry.py 工具注册表；results.py 结果类型；fake/ 四个假工具
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
