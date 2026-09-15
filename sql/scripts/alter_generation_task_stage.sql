-- ---------------------------------------------------------------------------
-- 阶段 0：给 generation_task 增加「Agent 流水线阶段」三列
--
-- 为什么必须手写 ALTER：
--   建表走的是 Base.metadata.create_all()，它**只能建新表、不会改已存在的表**。
--   （docs/proj_progress.md 里已经记过一次同样的坑：token 三列也是手写 ALTER 加的。）
--
-- 列的含义：
--   stage       —— Agent 流水线阶段（queued/routing/digesting/retrieving/planning/generating/done/failed）
--                  与 status 正交：status 是"活着还是结束了"，stage 是"走到哪一步了"
--   stageDetail —— 给用户看的一句话进展
--   progress    —— 进度百分比 0~100（失败时保留失败时的值，不归零）
--
-- 执行时间：2026-09-15（已在本机 wgp_db 执行完毕，本文件用于留档与换机重建）
-- ---------------------------------------------------------------------------

ALTER TABLE generation_task
  ADD COLUMN stage       VARCHAR(16)  NOT NULL DEFAULT 'queued'
      COMMENT 'Agent阶段:queued/routing/digesting/retrieving/planning/generating/done/failed' AFTER status,
  ADD COLUMN stageDetail VARCHAR(255) NULL
      COMMENT '阶段明细文案(给用户看)' AFTER stage,
  ADD COLUMN progress    SMALLINT     NOT NULL DEFAULT 0
      COMMENT '进度百分比0-100' AFTER stageDetail;

-- 历史数据回填：不回填的话，已有记录会全部显示成"排队中"
UPDATE generation_task SET stage = 'done',   progress = 100 WHERE status = 'success';
UPDATE generation_task SET stage = 'failed', progress = 0   WHERE status = 'failed';

-- 历史僵尸 running 记录：与 worker 启动时的「僵尸回收」语义保持一致
UPDATE generation_task SET stage = 'failed', progress = 0   WHERE status = 'running';
