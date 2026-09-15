-- 阶段 6：generation_task 增加 sessionUuid 列（agent 模式带附件时用）
--
-- 为什么需要它：agent 模式的附件与别名都挂在**会话**上（@docN 的作用域是会话），
-- 生成时必须按会话取回附件 digest。跨库只存 id，不建外键、不 JOIN（§3.4 铁律）。
--
-- ⚠️ 本表用 create_all 建表，而 create_all **不能改已存在的表**，所以这里手写 DDL。
-- 执行（MySQL，库为 .env 里的 MYSQL_DB_NAME）：
--   mysql -u <user> -p <db> < sql/scripts/alter_generation_task_session.sql
-- 或直接用项目内的引擎执行（见 docs/proj_progress.md 的说明）。

ALTER TABLE `generation_task`
    ADD COLUMN `sessionUuid` VARCHAR(64) NULL COMMENT '来源会话标识(agent_session.session_uuid)'
    AFTER `genType`;
