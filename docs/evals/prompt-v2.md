# Eval report: prompt-v2

- Run: 2026-09-17 04:09 UTC, 391s wall, concurrency 10
- Model under test: `deepseek-flash` at `api.deepseek.com`
- Judge: `deepseek-v4-pro`, reply-rubric-v1
- Cases: 52, repeated 3x
- Reference corpus: 48 sections, bm25_only
- Usage: 2060 model requests, 9,870,076 input tokens, 600,346 output tokens
- Prompts: orchestrator `412813ca4e5b (evals/prompts/orchestrator-v2.md)`, router `4c4be83df19e (built-in)`, planner `bbef233aeb13 (built-in)`, executor `d73ee84ee765 (built-in)`

## Pass rate by category

| Category | Runs | Passed | Failed | Errors | Pass rate |
|---|---:|---:|---:|---:|---:|
| routing | 45 | 43 | 2 | 0 | 96% |
| tool_calls | 39 | 39 | 0 | 0 | 100% |
| completion | 30 | 20 | 10 | 0 | 67% |
| robustness | 42 | 40 | 2 | 0 | 95% |
| **all** | 156 | 142 | 14 | 0 | 91% |

Pass rate excludes errors (the endpoint failed, not the agents).

## Judge scores

22 judged run(s), 22 passed the rubric (every criterion >= 3, mean >= 4).

| Criterion | Mean |
|---|---:|
| addresses_request | 3.91 |
| clear_next_step | 5.00 |
| honest_status | 5.00 |
| concise | 4.73 |

## Flaky cases

- `complete-workflow-answers-then-done`: 1/3 runs passed
- `complete-workflow-format-change-at-review`: 1/3 runs passed
- `complete-workflow-three-formats`: 2/3 runs passed
- `complete-workflow-two-part-series`: 1/3 runs passed
- `robust-unidentified-product-asks`: 1/3 runs passed
- `routing-direct-brief-then-outline`: 1/3 runs passed

## Failures

### `complete-workflow-answers-then-done` (completion, failed), run 1

A series that starts vague gets its answers in turn two and then completes.

- Failed: final: every stage in done. Saw: stage_01=done, stage_02=waiting_user, stage_03=pending, stage_04=pending
- Failed: final: >= 2 draft(s) produced. Saw: none
- Failed: final: every render format in html. Saw: no render

Turn 1 (user): “Plan a content series about https://example.com/p/47.”
- router: submit_route
- planner: search_references → submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner
- sent “dispatch_router {"request": "Plan a content series about https://example.com/p/47."}”
- sent “dispatch_planner {"goal": "Produce a content series of several pieces based on the source at https://example.com/p/47. The user gave no audience, tone or format details, so treat those as open."}”
- reply: “Before planning can continue, please answer: 1. Who is the audience for this content series (role, expertise level, and what they should be able to do after reading)? 2. What tone and voice should the pieces use (e.g. neutral/instructional, conversational, authoritative, playful)? 3. What output format do you want each piece rendered in (e.g. blog post, newsletter email, LinkedIn posts, slides, P…”

Turn 2 (user): “Audience: developers. Tone: playful. Two articles, each rendered as HTML. Please show me the plan.”
- planner: search_references ×2 → plan_write_stage_contract → plan_write_stage_contract!not_found → plan_write_stage_contract ×3 → submit_plan
- orchestrator: dispatch_planner → plan_get_stage_detail → plan_update_stage_state(waiting_user) → plan_get_stage_detail ×3
- sent “dispatch_planner {"goal": "Produce a content series of several pieces based on the source at https://example.com/p/47.", "answers": ["Audience: developers.", "Tone: playful.", "Output format: each piece rendered as HTML.", "Two articles (t…”
- reply: “Here's the plan for your two-article series (plan `plan_0001`, rev 7). Nothing is executed yet — stage 1 is waiting on your approval. **Series brief** - Source: https://example.com/p/47 - Audience: developers - Tone: playful, but technically accurate - Deliverables: two articles, each rendered as HTML, delivered as one batch **Stage 1 — Fetch the source brief** *[waiting on you]* - Goal: store th…”

Turn 3 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → fetch_brief → asset_get!not_found → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Fetch the source content at https://example.com/p/47 into the workspace as a stored brief, so later stages have the raw material for a two-piece series for developers.", "stage_id": "stage_01"}”
- reply: “**Stage 1 done** — the source is now stored as brief `brief_0001` (160 words, 40 points captured, stored as-is from https://example.com/p/47). One gap worth flagging: the fetch tool returned no section-heading list, and no asset exposes them, so the brief's headings could not be reported. The stage contract asked for them. I'll carry on for now — the outline stage reads the brief content itself, …”

Turn 4 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → write_outline(4) → asset_get!not_found ×2 → write_outline(4) → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Write a section outline for the stored brief that splits its coverage into exactly two articles for a developer audience, with playful headlines and 3-5 sections per article.", "stage_id": "stage_02"}”
- reply: “Stage 2 didn't meet its contract — the work item `outline_series_split` failed, so I've stopped short of marking it done. **What happened:** `write_outline` only takes a brief id and a section count, and returns an opaque id with a count and a generic summary. Running it twice on `brief_0001` produced `outline_0001` and `outline_0002` (4 sections each), but neither can carry article titles, playf…”

Turn 5 (approval loop): “Approved. Please continue.”
- reply: “"Approved" doesn't tell me which of the three paths you want, and they lead to very different places — one rewrites stage 2's contract, one accepts the partial outlines as they are, and one needs you to supply a tool or the missing `outline-patterns` / `tone-playful` assets before I can do anything at all. I won't pick for you. Reply with **1**, **2**, or **3**: 1. **Relax stage 2's contract** to…”

### `complete-workflow-answers-then-done` (completion, failed), run 2

A series that starts vague gets its answers in turn two and then completes.

- Failed: final: every stage in done. Saw: stage_01=done, stage_02=blocked, stage_03=pending
- Failed: final: every render format in html. Saw: no render

Turn 1 (user): “Plan a content series about https://example.com/p/47.”
- router: submit_route
- planner: submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner
- sent “dispatch_router {"request": "Plan a content series about https://example.com/p/47."}”
- sent “dispatch_planner {"goal": "Produce a content series based on the source https://example.com/p/47. The user has not specified the audience, tone or output format; the planner should ask if these are needed, or make them explicit in the plan…”
- reply: “Before planning can continue, please answer: 1. Who is the audience for this content series (e.g. role, expertise level, or segment)? 2. What tone should the series use (e.g. neutral/analytical, conversational, promotional)? 3. What output format should each piece be rendered in (e.g. blog post, HTML page, PDF, slide deck)? 4. How many pieces should the series contain, and how many stages/words p…”

Turn 2 (user): “Audience: developers. Tone: playful. Two articles, each rendered as HTML. Please show me the plan.”
- planner: search_references ×4 → plan_write_stage_contract ×4 → submit_plan
- orchestrator: dispatch_planner → plan_update_stage_state(waiting_user) → plan_get_stage_detail ×2
- sent “dispatch_planner {"goal": "Produce a content series based on the source https://example.com/p/47: two articles for a developer audience, written in a playful tone, each rendered as HTML.", "answers": ["Audience: developers", "Tone: playful…”
- reply: “Here's the plan for your two-article series, authored from https://example.com/p/47 — developer audience, playful tone, HTML output. **stage_01 — Source brief and two outlines** *(awaiting your review)* - Goal: Fetch the source brief from https://example.com/p/47 and produce two distinct section outlines for a two-article series aimed at developers in a playful tone. - Work items: `fetch-source-b…”

Turn 3 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → fetch_brief → asset_get!not_found → write_outline(4) ×2 → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Fetch the source brief from https://example.com/p/47 and produce two distinct section outlines for a two-article series aimed at developers in a playful tone.", "stage_id": "stage_01"}”
- reply: “Stage 1 is done — the brief from https://example.com/p/47 is stored as **brief_0001** (40 points), and both outlines exist: **outline_0001** (article 1, 4 sections) and **outline_0002** (article 2, 4 sections, a separate outline). All three work items completed; nothing failed. One note from the run: the contract's source refs (developer-audience, playful-tone, outline-pattern rules) aren't activ…”

Turn 4 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → write_draft(playful)!not_found ×2 → write_draft(playful) ×2 → plan_attach_runtime → submit_execution → plan_get_stage_detail → write_draft(playful) ×2 → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor ×2 → plan_update_stage_state(blocked)
- sent “dispatch_executor {"goal": "Write the full drafts of both articles, each following its own stored outline, in a playful voice for a developer audience.", "stage_id": "stage_02"}”
- sent “dispatch_executor {"goal": "Write the full drafts of both articles, each following its own stored outline, in a playful voice for a developer audience.", "stage_id": "stage_02", "retry_ids": ["draft-article-1", "draft-article-2"]}”
- reply: “I couldn't get stage_02 to meet its acceptance: both the original run and the retry produced 264-word drafts against the required 800–1,500 words, and `write_draft` has no length control while the section scope is fixed by the stage_01 outlines — so the fix isn't inside stage_02. The drafts (draft_0003 and draft_0004, one per outline, 4 sections each) exist but are short, and I've left stage_02 *…”

### `complete-workflow-chinese-series` (completion, failed), run 1

A series requested and approved in Chinese completes.

- Failed: final: every stage in done. Saw: stage_01=done, stage_02=done, stage_03=waiting_user, stage_04=pending
- Failed: final: every render format in html. Saw: no render

Turn 1 (user): “围绕 https://example.com/p/50 写两篇面向开发者的文章，语气轻松，输出 HTML。开始之前先给我看计划。”
- router: submit_route
- planner: search_references ×2 → plan_write_stage_contract ×4 → submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner → plan_get_stage_detail → plan_update_stage_state(waiting_user) → plan_get_stage_detail ×3
- sent “dispatch_router {"request": "围绕 https://example.com/p/50 写两篇面向开发者的文章，语气轻松，输出 HTML。开始之前先给我看计划。"}”
- sent “dispatch_planner {"goal": "围绕来源 https://example.com/p/50 产出两篇文章的交付物，读者是开发者，语气轻松，最终以 HTML 格式输出。计划需覆盖从获取 brief 到渲染 HTML 的完整流程，并容纳两篇独立的文章。"}”
- reply: “计划已经写好了（plan_0001，rev 5），先给你过目，没有你的明确同意我不会开始执行。 **目标**：围绕 https://example.com/p/50 产出两篇面向开发者的文章，语气轻松，并渲染为 HTML。 **阶段安排（共 4 步，顺序执行）** 1. **stage_01 — 两份独立的 brief** - `fetch-brief-a`：从 https://example.com/p/50 抓取 brief，存为文章 A 的 brief - `fetch-brief-b`：再次抓取同一来源，存为文章 B 的**另一份** brief（两篇不共用同一份） - 验收：工作区里存在两份可追溯到该来源的不同 brief 2. **stage_02 — 每个 brief 一份章节大纲** - `outline-a` / `outline-b`：面向开发者读者、轻松俏皮的语气，…”

Turn 2 (approval loop): “同意，继续。”
- executor: plan_get_stage_detail → fetch_brief ×2 → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Store two independent content briefs derived from the source https://example.com/p/50, one per planned article, so each article builds on its own brief.", "stage_id": "stage_01"}”
- reply: “stage_01 已完成，两份 brief 都存好了。 **已完成：stage_01 — 两份独立 brief** - `brief_0001`（对应文章 A）— 摘自 https://example.com/p/50，捕捉 40 个要点 - `brief_0002`（对应文章 B）— 同样来自该来源，但是**另一份**独立存储的 brief - 两篇不会共用同一份 brief，符合验收条件 **下一步：stage_02 — 每个 brief 一份章节大纲**（需要你确认后再执行） - `outline-a`：为 brief A 写章节大纲，面向开发者、轻松语气，A 有自己的角度 - `outline-b`：为 brief B 写章节大纲，角度与 A 明显不同，不复用 A 的章节列表 - 验收：两份大纲，各有有序章节列表与明确角度，两者可见地不同 两个问题想确认一下： 1. 两篇的角度希…”

Turn 3 (approval loop): “同意，继续。”
- executor: plan_get_stage_detail → write_outline(3) ×2 → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Produce one section outline per stored brief, giving two independent article structures written for a developer audience in a light, casual voice.", "stage_id": "stage_02"}”
- reply: “stage_02 也完成了。 **已完成：stage_02 — 两份大纲** - `outline_0001`（来自 brief_0001，3 个章节）→ 文章 A - `outline_0002`（来自 brief_0002，3 个章节）→ 文章 B - 两份大纲各自独立，角度不重叠 **下一步：stage_03 — 两份完整草稿**（等你确认后执行） - `draft-a`：严格按 outline_0001 写全文；温暖俏皮但不堆形容词、不用感叹号、不写空泛好处；800–1500 词；用真实标题和列表 - `draft-b`：严格按 outline_0002 写全文；同样轻松但保持技术准确；800–1500 词；不抄 draft-a 的段落 - 验收：两份草稿，各只有一个一级标题，字数在区间内 两点确认： 1. 这两篇有希望优先突出的重点吗？（没有我就按大纲原样展开） 2. 800–…”

Turn 4 (approval loop): “同意，继续。”
- executor: plan_get_stage_detail → write_draft(playful) ×2 → asset_get!not_found ×3 → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_get_stage_detail → asset_list → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Write two complete article drafts, one per outline, in a light developer-facing voice and in a shape that can be rendered as HTML.", "stage_id": "stage_03"}”
- reply: “stage_03 跑完了，但**验收没达标**，我停下来找你确认，没有把它标记为完成。 **结果** - `draft_0001`（按 outline_0001，3 个章节，语气 playful）— 文章 A - `draft_0002`（按 outline_0002，3 个章节，语气 playful）— 文章 B - 问题：两份都只有约 **198 词**，而验收要求是每篇 800–1500 词。 **为什么**：`write_draft` 只能接受 outline_id / 语气 / 参考资产，没有办法控制篇幅或标题结构，用同样参数重跑会得到同样的 198 词，所以重试没有意义。另外我确认了会话里**没有任何可用资产**（`asset_list` 为空），stage_03 引用的 `tone-playful`、`audience-developers`、`format-html-ar…”

Turn 5 (approval loop): “同意，继续。”
- reply: “抱歉，「同意，继续」在这三种走法里没法确定你选的是哪一个——它们的结果差别很大（改验收标准 / 直接交付 198 词的成品 / 等你给资产），我不能替你挑，否则就等于替你批准了。 回复数字即可： 1. **改验收标准**：让 Planner 把 stage_03 的验收改成「覆盖各自大纲 + 轻松语气 + 结构完整」，去掉 800–1500 词硬指标，然后重跑两份草稿。 2. **照现状往下走**：接受这两份约 198 词的草稿，进入 stage_04 渲染成 HTML 交给你（成品会明显偏短）。 3. **补足字数**：你提供 `tone-playful` / `format-html-article` 这类语气、格式规则作为会话资产（或告诉我可用的资产名），我带上资产重跑草稿。 （如果你只是想让我拿主意：我倾向 1，因为 2 交出去的会是不达标的成品，而 3 取决于你手上有没有这些资…”

### `complete-workflow-chinese-series` (completion, failed), run 2

A series requested and approved in Chinese completes.

- Failed: final: every stage in done. Saw: stage_01=done, stage_02=blocked, stage_03=pending

Turn 1 (user): “围绕 https://example.com/p/50 写两篇面向开发者的文章，语气轻松，输出 HTML。开始之前先给我看计划。”
- router: submit_route
- planner: search_references ×2 → plan_write_stage_contract ×3 → submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner → plan_update_stage_state(waiting_user)
- sent “dispatch_router {"request": "围绕 https://example.com/p/50 写两篇面向开发者的文章，语气轻松，输出 HTML。开始之前先给我看计划。"}”
- sent “dispatch_planner {"goal": "Create two separate articles for an audience of developers, sourced from https://example.com/p/50. Both articles must be written in a playful, light tone and delivered as HTML output. Each article is its own fina…”
- reply: “计划已经拟好，请你过目： **计划 `plan_0001`** — 围绕 https://example.com/p/50 产出两篇面向开发者的文章，语气轻松，输出 HTML。 | 顺序 | 阶段 | 目标 | |---|---|---| | 1 | `stage_01` | 抓取并保存来自 https://example.com/p/50 的源 brief，让两篇文章共用同一份素材。 | | 2 | `stage_02` | 产出文章 1（独立交付物）：面向开发者的 HTML 文章，轻松、轻快的语气，基于已保存的 brief；角度是一篇偏实用的讲解文。 | | 3 | `stage_03` | 产出文章 2（独立交付物）：第二篇独立成篇的开发者 HTML 文章，同样轻松语气，但取一个明显不同的角度——常见坑与取舍。 | 几个说明： - 两篇文章都会先基于第 1 步存下的同一份 brie…”

Turn 2 (approval loop): “同意，继续。”
- executor: plan_get_stage_detail → fetch_brief → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_get_stage_detail → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Fetch and store the source brief from https://example.com/p/50 so both articles can be written from the same material.", "stage_id": "stage_01"}”
- reply: “第 1 步已完成：源 brief 已抓取并存为 `brief_0001`（160 词、40 条要点，原文保留未改写），两篇文章都会基于它来写。 接下来是第 2 步，这一步也需要你点头我才开工： **`stage_02`（顺序 2）— 产出文章 1（独立交付物）** 目标是面向开发者、基于已存 brief 的一篇 HTML 文章，轻松轻快的语气，角度是**上手型实用讲解**（它是什么、怎么运作、怎么试）。 包含 3 个工作项： 1. `outline-article-1` — 为 brief 写分节提纲，覆盖上述角度；标题要俏皮，但事实表述必须准确。 2. `draft-article-1` — 依提纲写全文；受众开发者，语气轻松温暖、不对读者的问题开玩笑；约 800–1200 词；不与文章 2 的角度或措辞重复。 3. `render-article-1` — 渲染成 HTML 交付物：…”

Turn 3 (approval loop): “同意，继续。”
- executor: plan_get_stage_detail → write_outline(4) → write_draft(playful) ×2 → render_output(html) → plan_attach_runtime → submit_execution → plan_get_stage_detail → write_draft(playful) → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor ×2 → plan_update_stage_state(blocked)
- sent “dispatch_executor {"goal": "Produce Article 1 as its own finished deliverable: a developer-facing HTML article in a playful, light tone, built from the stored brief.", "stage_id": "stage_02"}”
- sent “dispatch_executor {"goal": "Produce Article 1 as its own finished deliverable: a developer-facing HTML article in a playful, light tone, built from the stored brief.", "stage_id": "stage_02", "retry_ids": ["draft-article-1"]}”
- reply: “文章 1 这一步卡住了，原因一句话：`write_draft` 没有篇幅参数，两次尝试都只产出 264 词（`draft_0002`、`draft_0003`），达不到该阶段「约 800–1200 词」的验收线，重试解决不了；另外当前的 HTML 渲染 `render_0001` 对应的是上一稿 `draft_0002`，和最新草稿不一致。 现在可以走的路（请你选一个）： 1. **改计划再试**：让 Planner 重写 `stage_02`，把 4 节提纲拆成更多小节、或把篇幅目标下调到工具实际能到的量级，然后重新执行（并重新渲染以对齐最新草稿）。 2. **放宽篇幅要求，接受现状**：把验收标准从 800–1200 词改为约 250–300 词，以 `draft_0003` 为准重新渲染 HTML，然后交付文章 1。 3. **暂停**：保持 `stage_02` 阻塞状态，先不动…”

### `complete-workflow-chinese-series` (completion, failed), run 3

A series requested and approved in Chinese completes.

- Failed: final: every stage in done. Saw: stage_01=done, stage_02=pending, stage_03=pending, stage_04=pending
- Failed: final: >= 2 draft(s) produced. Saw: none
- Failed: final: every render format in html. Saw: no render

Turn 1 (user): “围绕 https://example.com/p/50 写两篇面向开发者的文章，语气轻松，输出 HTML。开始之前先给我看计划。”
- router: submit_route
- planner: search_references ×3 → plan_write_stage_contract ×4 → submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner → plan_get_stage_detail ×4 → plan_update_stage_state(waiting_user)
- sent “dispatch_router {"request": "围绕 https://example.com/p/50 写两篇面向开发者的文章，语气轻松，输出 HTML。开始之前先给我看计划。"}”
- sent “dispatch_planner {"goal": "基于源 https://example.com/p/50 产出两篇面向开发者的文章，共用一个 brief；每篇都要有草稿并以 HTML 格式渲染输出。受众是开发者，语气轻松（playful）。最终交付物：两篇文章的 HTML 输出。"}”
- reply: “计划已经拟好，请你先过目（plan_0001，共 4 个阶段）： **阶段 1（order 1）— 抓取共享 brief** - 目标：从 https://example.com/p/50 抓取内容 brief 并存进工作区，供两篇文章共用。 - 工作项：`fetch-brief` — 用 fetch_brief 抓取并存为唯一的共享 brief。 - 验收：工作区中存有一份来自该 URL 的 brief，后续阶段可读取。 **阶段 2（order 2）— 文章 1 的大纲 + 全文草稿** - 目标：基于共享 brief，为开发者写文章 1 的分节大纲与完整草稿，语气轻松。 - 工作项：`outline-article-1`（只覆盖文章 1 负责的那部分主题，一节一个想法，标题遵循系列命名并含 part 编号）；`draft-article-1`（按大纲逐节成文，语气温暖、轻快，内容具…”

Turn 2 (approval loop): “同意，继续。”
- executor: plan_get_stage_detail → fetch_brief → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done)
- sent “dispatch_executor {"goal": "Fetch the shared content brief for both articles from the source URL https://example.com/p/50 and store it in the workspace.", "stage_id": "stage_01"}”
- reply: “阶段 1 完成。 **阶段 1 结果**：已从 https://example.com/p/50 抓到共享 brief，存为 `brief_0001`（160 词，40 个要点）。两篇文章都会用它作为唯一素材来源。 **下一步：阶段 2 — 文章 1 的大纲 + 全文草稿** - 目标：基于 `brief_0001`，为开发者写文章 1 的分节大纲与完整草稿，语气轻松。 - 工作项： - `outline-article-1`：只覆盖文章 1 负责的主题切片，一节一个想法，标题遵循系列命名并含 part 编号。 - `draft-article-1`：按大纲逐节成文，温暖轻快的语气，具体准确；不用最高级形容词、不用感叹号、不用空泛营销话术。 - 依赖：阶段 1（已完成）。 要我开始执行阶段 2 吗？回复确认即可。”

### `complete-workflow-format-change-at-review` (completion, failed), run 1

A change requested during plan review is replanned, then produced that way.

- Failed: final: every stage in done. Saw: stage_01=done, stage_02=waiting_user, stage_03=pending, stage_04=pending
- Failed: final: >= 1 render(s) produced. Saw: none
- Failed: final: every render format in pdf. Saw: no render

Turn 1 (user): “Write a two-part series about https://example.com/p/48 for managers, formal tone, as HTML. Show me the plan first.”
- router: submit_route
- planner: search_references ×4 → plan_write_stage_contract ×4 → submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner → plan_get_stage_detail ×4 → plan_update_stage_state(waiting_user)
- sent “dispatch_router {"request": "Write a two-part series about https://example.com/p/48 for managers, formal tone, as HTML. Show me the plan first."}”
- sent “dispatch_planner {"goal": "Produce a two-part content series based on the brief fetched from https://example.com/p/48. Deliverables: two separate drafts (part 1 and part 2), each rendered as HTML. Audience: managers. Tone: formal. Format: …”
- reply: “Here's the plan for your review — nothing runs until you approve it. **Objective:** Produce a two-part content series based on https://example.com/p/48, written for managers in a formal tone, and rendered as HTML. **Stage 1 — Source brief and two-part outline** *(awaiting your approval)* - *Fetch the brief* — pull the content brief from https://example.com/p/48 and store it; outlining starts only…”

Turn 2 (user): “Change of plan: render everything as PDF instead of HTML. Show me the updated plan before starting.”
- planner: plan_get_stage_detail → search_references ×3 → plan_write_stage_contract → submit_plan
- orchestrator: dispatch_planner → plan_get_stage_detail
- sent “dispatch_planner {"goal": "Update the plan objective: the two-part series based on https://example.com/p/48 for managers in a formal tone is now to be rendered as PDF, not HTML. Rewrite the render stage so it renders both stored drafts as …”
- reply: “Updated — only the render stage changed; stages 1–3 are untouched. **Objective:** Produce a two-part content series based on https://example.com/p/48, written for managers in a formal tone, and rendered as **PDF**. **Stage 1 — Source brief and two-part outline** *(awaiting your approval)* - *Fetch the brief* — pull the content brief from https://example.com/p/48 and store it. - *Write the two-par…”

Turn 3 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → fetch_brief → write_outline(6) → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Fetch the source brief from https://example.com/p/48 and turn it into a section outline that splits the material into two standalone parts aimed at managers.", "stage_id": "stage_01"}”
- reply: “**Stage 2 — Part 1 draft** *(awaiting your go-ahead)* - *Write part 1 draft* — a complete draft following only the part 1 sections of `outline_0001`, in a precise, neutral and impersonal formal voice; no rhetorical questions, humour or intensifiers. It opens with a short section restating the context part 1 needs (a reader may arrive here first) and notes at the end that part 2 follows. No part 2…”

Turn 4 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → asset_get!not_found ×2 → write_draft(formal) → submit_execution → plan_get_stage_detail → write_draft(formal)!not_found → write_draft(formal) → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor ×2 → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Produce part 1 of the series as a complete draft that follows the part 1 sections of the stored outline.", "stage_id": "stage_02"}”
- sent “dispatch_executor {"goal": "Produce part 1 of the series as a complete draft that follows the part 1 sections of the stored outline.", "stage_id": "stage_02", "retry_ids": ["draft_part1"]}”
- reply: “Part 1 is drafted, but I need your call before I accept it. **What happened:** the first run failed; the retry produced `draft_0002` (396 words, formal). The problem is scope — the stored outline `outline_0001` holds all six sections for both parts, and the drafting tool writes from an outline in full with no way to restrict it to part 1's sections. So `draft_0002` is almost certainly the whole s…”

Turn 5 (approval loop): “Approved. Please continue.”
- reply: “Approved alone doesn't tell me which of the two paths you meant — and they produce different results, so I don't want to guess. Stage 2 is sitting on a result review for `draft_0002`, which was written from the full two-part outline and therefore likely contains part 2's material. - If you meant **option 1**: I replan stage 1 to store the outline per part and rewrite part 1 cleanly against it (pa…”

### `complete-workflow-format-change-at-review` (completion, failed), run 2

A change requested during plan review is replanned, then produced that way.

- Failed: final: every stage in done. Saw: stage_01=done, stage_02=done, stage_03=blocked, stage_04=pending
- Failed: final: >= 1 render(s) produced. Saw: none
- Failed: final: every render format in pdf. Saw: no render

Turn 1 (user): “Write a two-part series about https://example.com/p/48 for managers, formal tone, as HTML. Show me the plan first.”
- router: submit_route
- planner: search_references ×4 → plan_write_stage_contract ×4 → submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner → plan_get_stage_detail → plan_update_stage_state(waiting_user)
- sent “dispatch_router {"request": "Write a two-part series about https://example.com/p/48 for managers, formal tone, as HTML. Show me the plan first."}”
- sent “dispatch_planner {"goal": "Produce a two-part content series based on the source https://example.com/p/48. Audience: managers. Tone: formal. Final deliverables: HTML output, one per part (two HTML deliverables total)."}”
- reply: “Here's the plan for your approval. **Plan plan_0001 — two-part series, manager audience, formal tone, HTML output** | # | Stage | Goal | |---|-------|------| | 1 | stage_01 | Store the source brief for the two-part series in the workspace so downstream stages work from a single stored brief. | | 2 | stage_02 | Produce one section outline for the two-part series that assigns content to part 1 and …”

Turn 2 (user): “Change of plan: render everything as PDF instead of HTML. Show me the updated plan before starting.”
- planner: search_references ×2 → plan_write_stage_contract → submit_plan
- orchestrator: dispatch_planner → plan_get_stage_detail
- sent “dispatch_planner {"goal": "Render each of the two stored drafts (part 1 and part 2 of the series) into its own PDF deliverable, giving two PDF outputs in total. Note: the user changed the required output format for the whole series from HT…”
- reply: “Plan updated — here it is for your approval before anything runs. **Plan plan_0001 (rev 6) — two-part series about https://example.com/p/48 — managers, formal tone, PDF output** | # | Stage | State | Goal | |---|-------|-------|------| | 1 | stage_01 | waiting for your approval | Store the source brief for the two-part series in the workspace so downstream stages work from a single stored brief. …”

Turn 3 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → fetch_brief → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_get_stage_detail → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Store the source brief for the two-part series in the workspace so downstream stages work from a single stored brief.", "stage_id": "stage_01"}”
- reply: “Stage 1 is done, and stage 2 is now up for your review. **Completed: stage_01** — the source brief from https://example.com/p/48 is stored in the workspace as **brief_0001** (40 points, original text and structure intact). Stage 2 reads from this single stored brief. **Next up for approval: stage_02 — series section outline** | # | Work item | What it does | |---|-----------|--------------| | 1 |…”

Turn 4 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → write_outline(6) → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_get_stage_detail → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Produce one section outline for the two-part series that assigns content to part 1 and part 2 and gives each part a self-contained structure.", "stage_id": "stage_02"}”
- reply: “Stage 2 is done, and stage 3 is up for your review. **Completed: stage_02** — one section outline for the series is stored as **outline_0001** (6 sections planned from brief_0001, split across part 1 and part 2). Both drafts will follow it. **Next up for approval: stage_03 — two full drafts** | # | Work item | What it does | |---|-----------|--------------| | 1 | *Write draft for part 1* | Write …”

Turn 5 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → asset_get!not_found ×3 → write_draft(formal) → plan_attach_runtime → submit_execution → plan_get_stage_detail → asset_get!not_found ×5 → write_draft(formal) → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor ×2 → plan_update_stage_state(blocked)
- sent “dispatch_executor {"goal": "Produce two full drafts — part 1 and part 2 of the series — each following the stored outline.", "stage_id": "stage_03"}”
- sent “dispatch_executor {"goal": "Produce two full drafts — part 1 and part 2 of the series — each following the stored outline.", "stage_id": "stage_03", "retry_ids": ["wi-draft-part1", "wi-draft-part2"]}”
- reply: “Stage 3 stopped, and stage 4 can't proceed until it's resolved. **What happened:** stage_03 is now **blocked**. It needs to produce two *part-scoped* drafts, but stage_02 stored **one** whole-series outline (outline_0001, 6 sections spanning both parts) and the drafting tool accepts only an outline id — there's no way to scope a draft to a part or a section range. Both attempts (initial run and t…”

### `complete-workflow-three-formats` (completion, failed), run 2

Three formats from three drafts all get produced.

- Failed: final: every stage in done. Saw: stage_01=done, stage_02=done, stage_03=done, stage_04=blocked
- Failed: final: >= 3 render(s) produced. Saw: none

Turn 1 (user): “For https://example.com/p/46, produce an HTML article, a PDF datasheet and a short video for prospective buyers, each from its own draft. Neutral tone. Let me review the plan first.”
- router: submit_route
- planner: search_references ×2 → plan_write_stage_contract ×4 → submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner → plan_get_stage_detail ×4 → plan_update_stage_state(waiting_user)
- sent “dispatch_router {"request": "For https://example.com/p/46, produce an HTML article, a PDF datasheet and a short video for prospective buyers, each from its own draft. Neutral tone. Let me review the plan first."}”
- sent “dispatch_planner {"goal": "Produce three deliverables for prospective buyers, sourced from https://example.com/p/46: (1) an HTML article, (2) a PDF datasheet, (3) a short video. Each deliverable must come from its own separate draft. Tone:…”
- reply: “Here's the plan for review — `plan_0001` (nothing has been run yet). **Objective:** three deliverables for prospective buyers from https://example.com/p/46 — an HTML article, a PDF datasheet and a short video — each built from its own draft, neutral tone. **Stage 1 — Source brief** Goal: the brief for https://example.com/p/46 stored in the workspace as the single factual input for all three deliv…”

Turn 2 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → fetch_brief → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_get_stage_detail → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Have the source brief for https://example.com/p/46 stored in the workspace as the single factual input for all three deliverables.", "stage_id": "stage_01"}”
- reply: “Stage 1 ran and completed: the brief for https://example.com/p/46 is now stored in the workspace as `brief_0001` (40 points, verbatim, no claims added). It's the factual input for all three deliverables. Before I run the next stage, it needs your go-ahead. **Stage 2 — Outlines** (order 2, now awaiting your review) Goal: three separate section outlines, one per deliverable, each derived from the s…”

Turn 3 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → asset_get!not_found ×3 → write_outline(2)!tool_failed → plan_get_stage_detail → write_outline(5) → write_outline(4) ×2 → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_get_stage_detail → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Three separate section outlines, one per deliverable (HTML article, PDF datasheet, short video), each derived from the stored brief and consistent with a neutral tone for prospective buyers.", "stage_id": "stage…”
- reply: “Stage 2 ran and completed. Three outlines now exist, all derived from `brief_0001`: - `outline_0001` — HTML article, 5 sections (headings/list/table slots only) - `outline_0002` — PDF datasheet, 4 sections (single spec table slot with units in the header row) - `outline_0003` — short video, 4 beats with a captions/transcript slot One thing to flag: the stage contract refers to its inputs by alias…”

Turn 4 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → asset_get!not_found ×7 → submit_execution → plan_get_stage_detail → asset_get!not_found ×10 → plan_attach_runtime → plan_get_stage_detail ×2 → write_draft(neutral) ×3 → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor ×2 → plan_update_stage_state(done) → plan_get_stage_detail → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Three separate full drafts, one per deliverable, each following its own outline and carrying the neutral prospective-buyer tone.", "stage_id": "stage_03"}”
- sent “dispatch_executor {"goal": "Three separate full drafts, one per deliverable, each following its own outline and carrying the neutral prospective-buyer tone.", "stage_id": "stage_03", "retry_ids": ["draft-article", "draft-datasheet", "draft…”
- reply: “Stage 3 is done. Worth noting: its first attempt failed — the executor found no upstream refs and no brief because the contract's input aliases (`1`, `2`) don't resolve. On retry it recovered the real ids by reading the earlier stages' details, so we now have three drafts, all neutral, each against its own outline: - `draft_0001` — article, 5 sections (from `outline_0001`) - `draft_0002` — datash…”

Turn 5 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → asset_get!not_found ×5 → plan_attach_runtime → submit_execution → plan_get_stage_detail → asset_get!not_found ×4 → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor ×2 → plan_update_stage_state(blocked)
- sent “dispatch_executor {"goal": "Three rendered deliverables, each rendered from its own draft: the article as HTML, the datasheet as PDF, the video as a short video file.", "stage_id": "stage_04"}”
- sent “dispatch_executor {"goal": "Three rendered deliverables, each rendered from its own draft: the article as HTML, the datasheet as PDF, the video as a short video file.", "stage_id": "stage_04", "retry_ids": ["render-html-article", "render-p…”
- reply: “I couldn't complete the renders: stage 4's contract carries an unresolved input alias (`3`) and empty upstream refs, so the three drafts (`draft_0001`, `draft_0002`, `draft_0003` — all made in stage 3) never reached the renderer, and `render_output` can only work from a stored `draft_id`. I retried all three render items once; still nothing was produced, so stage 4 is now `blocked`. A second issu…”

### `complete-workflow-two-part-series` (completion, failed), run 1

A reviewed two-part series ends with every stage done and two drafts.

- Failed: final: every stage in done. Saw: stage_01=done, stage_02=blocked, stage_03=pending
- Failed: final: >= 1 render(s) produced. Saw: none
- Failed: final: every render format in html. Saw: no render

Turn 1 (user): “Write a two-part series about https://example.com/p/45 for developers, playful tone, published as HTML. Show me the plan before you start.”
- router: submit_route
- planner: search_references ×3 → plan_write_stage_contract ×3 → submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner → plan_get_stage_detail ×3 → plan_update_stage_state(waiting_user)
- sent “dispatch_router {"request": "Write a two-part series about https://example.com/p/45 for developers, playful tone, published as HTML. Show me the plan before you start."}”
- sent “dispatch_planner {"goal": "Produce a two-part article series based on the source https://example.com/p/45. Audience: developers. Tone: playful. Each part published as an HTML output."}”
- reply: “Here's the plan I'd run for your two-part series. Nothing has been executed yet — I'll wait for your approval before starting. **Objective:** A two-part article series from https://example.com/p/45 — developer audience, playful tone, published as HTML. **Stage 1 — Research + shared outline** - Goal: A stored brief for the source, plus one shared outline covering both parts. - Work items: - *Fetch…”

Turn 2 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → fetch_brief → write_outline(6) → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "A stored brief for source https://example.com/p/45 plus one shared outline that covers both parts of the two-part series.", "stage_id": "stage_01"}”
- reply: “Stage 1 is done: the source brief (`brief_0001`, 40 points captured) and one shared outline (`outline_0001`, 6 sections) now cover both parts. Next up is Stage 2, which needs your go-ahead: **Stage 2 — Two drafts** - Goal: Two full drafts, one for Part 1 and one for Part 2, both derived from the shared brief and outline. - Work items: - *Draft Part 1* — follows only the Part 1 sections of `outlin…”

Turn 3 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → write_draft(playful) ×2 → asset_get!not_found ×2 → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(blocked)!revision_conflict → plan_get_stage_detail → plan_update_stage_state(blocked)
- sent “dispatch_executor {"goal": "Two full drafts, one for Part 1 and one for Part 2, both derived from the shared brief and outline.", "stage_id": "stage_02"}”
- reply: “Stage 2 is blocked rather than done — both drafts failed acceptance: each came out at 396 words (floor is 800) and, because the drafting tool only takes an `outline_id`, both drafts used the entire shared 6-section outline, so there's no distinct Part 1 and Part 2 (no part-scoped sections, no Part 1 hook/forward pointer, no Part 2 back-link), and the executor reports it has exhausted its remedies…”

### `complete-workflow-two-part-series` (completion, failed), run 2

A reviewed two-part series ends with every stage done and two drafts.

- Failed: final: every stage in done. Saw: stage_01=done, stage_02=done, stage_03=blocked
- Failed: final: >= 1 render(s) produced. Saw: none
- Failed: final: every render format in html. Saw: no render

Turn 1 (user): “Write a two-part series about https://example.com/p/45 for developers, playful tone, published as HTML. Show me the plan before you start.”
- router: submit_route
- planner: search_references ×3 → plan_write_stage_contract ×3 → submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner → plan_get_stage_detail → plan_update_stage_state(waiting_user) → plan_get_stage_detail ×2
- sent “dispatch_router {"request": "Write a two-part series about https://example.com/p/45 for developers, playful tone, published as HTML. Show me the plan before you start."}”
- sent “dispatch_planner {"goal": "Produce a two-part content series (part 1 and part 2) based on the source https://example.com/p/45. Audience: developers. Tone: playful. Each part must be delivered as a published HTML output (rendered). The seri…”
- reply: “Here's the plan for your two-part series — nothing has been written yet. **Plan `plan_0001` (rev 4)** — Two-part series about https://example.com/p/45 · developers · playful tone · HTML output **Stage 1 (order 1) — brief + shared series outline** ← *waiting for your approval* - Goal: one established brief and a single two-part outline that clearly allocates distinct material to part 1 and part 2.…”

Turn 2 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → fetch_brief → write_outline(2) → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(done) → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Established brief and a single two-part series outline that clearly allocates distinct material to part 1 and part 2, for a developer audience and a playful tone.", "stage_id": "stage_01"}”
- reply: “Stage 1 is done — here's what it produced, and stage 2 is now up for your approval. **Stage 1 complete** - `brief_0001` — shared source brief for https://example.com/p/45 (40 points captured) - `outline_0001` — one two-section series outline covering *part 1 of 2* and *part 2 of 2*, each part owning distinct key points, example/code angle and takeaway (2 sections, to match the two-part split) **S…”

Turn 3 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail ×2 → write_draft(playful)!not_found ×2 → write_draft(playful) ×2 → plan_attach_runtime → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor → plan_update_stage_state(waiting_user)
- sent “dispatch_executor {"goal": "Two separate full drafts — part 1 and part 2 — each following its own section of the shared series outline and each readable on its own.", "stage_id": "stage_02"}”
- reply: “Both drafts are written, but the stage is paused for your review because the Executor couldn't confirm one acceptance point from metadata alone. **Stage 2 results** - `draft_0001` — part 1 draft (playful, 2 sections), from `outline_0001` - `draft_0002` — part 2 draft (playful, 2 sections), from `outline_0001` **Two caveats to check before I mark this done:** 1. **Scope of the split** — the drafti…”

Turn 4 (approval loop): “Approved. Please continue.”
- orchestrator: plan_update_stage_state(done) → plan_update_stage_state(waiting_user)
- reply: “Stage 2 is marked done. One last approval gate before publishing. **Done so far:** stage 1 (brief + shared two-part outline) and stage 2 (`draft_0001` part 1, `draft_0002` part 2, both playful). **Stage 3 (order 3) — HTML publication** ← *waiting for your approval* - Goal: both parts published as rendered HTML outputs — one HTML deliverable per part. - Work items: 1. **Render part 1 as HTML** — r…”

Turn 5 (approval loop): “Approved. Please continue.”
- executor: plan_get_stage_detail → asset_get!not_found ×2 → submit_execution → plan_get_stage_detail → asset_get!not_found ×2 → submit_execution
- orchestrator: plan_update_stage_state(doing) → dispatch_executor ×2 → plan_update_stage_state(blocked)
- sent “dispatch_executor {"goal": "Both parts published as rendered HTML outputs — one HTML deliverable per part.", "stage_id": "stage_03"}”
- sent “dispatch_executor {"goal": "Both parts published as rendered HTML outputs — one HTML deliverable per part.", "stage_id": "stage_03", "retry_ids": ["render-part-1-html", "render-part-2-html"]}”
- reply: “Stage 3 is blocked, so neither part is published as HTML yet. **What happened:** both render work items failed twice. Stage 3's contract consumes input `"2"` — the part 1 and part 2 drafts — but that input is unresolved and the stage's upstream refs are empty, so no draft id reached the renderer. It couldn't call `render_output` without a real draft id, and it correctly refused to invent one. Not…”

### `robust-unidentified-product-asks` (robustness, failed), run 1

"Our new product" names nothing the tools can fetch; ask which product.

- Failed: turn 1: reply asks the user a question. Saw: I can't start on that yet — "our new product" doesn't give me anything to fetch a brief from (no URL, no product name or description). Could you give me one of…

Turn 1 (user): “Write an HTML article about our new product.”
- reply: “I can't start on that yet — "our new product" doesn't give me anything to fetch a brief from (no URL, no product name or description). Could you give me one of: - a URL for the product page or launch material, or - the product name plus a short description (what it is, who it's for, key features) Also helpful, if you have a preference: target audience and tone (neutral, playful, or formal). Once …”

### `robust-unidentified-product-asks` (robustness, failed), run 3

"Our new product" names nothing the tools can fetch; ask which product.

- Failed: turn 1: reply asks the user a question. Saw: I can't start on this one yet — the request doesn't name the product or give me anything I can fetch a brief from ("our new product" could be anything). Could …

Turn 1 (user): “Write an HTML article about our new product.”
- reply: “I can't start on this one yet — the request doesn't name the product or give me anything I can fetch a brief from ("our new product" could be anything). Could you give me one of these: - a URL for the product (landing page, spec sheet, or announcement), or - the product name plus a sentence or two on what it is, who it's for, and the key points you want covered. Once I have that, I'll pull a brie…”

### `routing-direct-brief-then-outline` (routing, failed), run 1

A brief and the outline built from it are still one pass.

- Failed: turn 1: router chooses direct. Saw: router chose workflow: The outline must be built from the fetched brief, so it needs the brief as an intermediate deliverable before the outline can be produced.
- Failed: turn 1: no plan is created. Saw: plan_0001 with 0 stage(s)

Turn 1 (user): “Get the brief for https://example.com/p/15 and write an outline from it.”
- router: submit_route
- planner: search_references ×2 → submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner
- sent “dispatch_router {"request": "Get the brief for https://example.com/p/15 and write an outline from it."}”
- sent “dispatch_planner {"goal": "Produce two deliverables for the source at https://example.com/p/15: (1) the fetched content brief stored in the workspace, and (2) a section outline written from that brief. No audience, tone or output format wa…”
- reply: “Before planning can continue, please answer: 1. Who is the audience for the brief and outline (e.g. developers, executives, general readers)? 2. What tone should the outline use (e.g. neutral/explanatory, technical, marketing)? 3. What output format do you want for the outline deliverable (e.g. plain markdown section list, numbered outline, outline plus word-count targets per section)?”

### `routing-direct-brief-then-outline` (routing, failed), run 2

A brief and the outline built from it are still one pass.

- Failed: turn 1: router chooses direct. Saw: router chose workflow: Two deliverables where the outline depends on the brief produced first, so the sequence needs chained steps rather than a single pass.
- Failed: turn 1: no plan is created. Saw: plan_0001 with 0 stage(s)

Turn 1 (user): “Get the brief for https://example.com/p/15 and write an outline from it.”
- router: submit_route
- planner: search_references ×2 → submit_plan
- orchestrator: dispatch_router → plan_create → dispatch_planner
- sent “dispatch_router {"request": "Get the brief for https://example.com/p/15 and write an outline from it."}”
- sent “dispatch_planner {"goal": "Deliver two things in order: (1) a content brief fetched from the source URL https://example.com/p/15, and (2) a section outline written from that brief. The user did not specify an audience, tone, or target form…”
- reply: “Before planning can continue, please answer: 1. Who is the audience for the section outline (e.g. beginners, practitioners, decision-makers)? 2. What tone should it use (e.g. formal, playful, neutral)? 3. What output format should the outline target (e.g. HTML article, Markdown document, slide deck)? 4. Should the brief-fetch step proceed now regardless of these answers, or wait until they are se…”
