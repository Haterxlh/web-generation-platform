'''智能体模块

规则：api → services → {repositories, agents} → {MySQL, LLM/磁盘}

agents/ 怎么把需求变成文件：提示词组装、链/图编排 不碰数据库、不碰 HTTP

为什么不能把 LangChain/LangGraph 直接写进 services/？ 
因为职责会糊在一起：以后想"换个提示词策略"或"给多文件图加个节点"时，你没法只动一处；
而想加"每天限 10 次生成"又得在一堆 model.invoke 里找地方插。

分开之后：
- 换模型 / 改编排 → 只动 agents/
- 加配额 / 会员校验 / 状态流转 → 只动 services/

落盘和落库都由 service 统一做，agents 只返回字典。
好处：agents/ 可以脱离 MySQL 单独测试（给一句话，看它返回的文件对不对），这是后面能写 mock 测试的前提。
'''