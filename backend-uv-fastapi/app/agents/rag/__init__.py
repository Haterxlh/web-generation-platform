"""个人 RAG 子包：**一期只做"判定"与"接口"，检索本身是桩**。

三块职责，边界必须清楚（docs/agent_refactor_plan.md 阶段 4）：

- ``need_rag``：判断"这次需求要不要用用户的私人知识库"，并给出**检索词**。
  这是一次真实的结构化模型调用 —— 判定逻辑一期就写完，产出也是真实结果；
- ``retriever``：`RetrieverProvider` 接口 + 桩 provider。一期**查不了**，
  但三态（hit / miss / skipped）从第一天就是真的，二期只替换 provider；
- 向量表、embedding、L1/L2 入库**都不在这里**：那是二期（华为云 BGE-M3 + pgvector）。

为什么一期就要把接口定死：二期换实现时**不允许**动图结构、不许动提示词骨架，
只允许换 `build_retriever_provider()` 的返回值。这条纪律只有在今天就写下接口时才守得住。
"""
