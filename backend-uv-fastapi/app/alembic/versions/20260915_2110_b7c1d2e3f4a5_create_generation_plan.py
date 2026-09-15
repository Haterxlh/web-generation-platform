"""create generation plan table

Revision ID: b7c1d2e3f4a5
Revises: eac80e932e7f
Create Date: 2026-09-15 21:10:00.000000

中文说明（alembic.ini 必须保持纯 ASCII，所以理由写在这里）：

阶段 5 新建 generation_plan 表，存两类东西：

1. 推理产物：FinalRequirement / FilePlan（JSONB）——
   它们属于 Agent 域，刻意不塞进 MySQL 的 generation_task；
2. 「预估 vs 实际」的对照字段：模型声明的难度、Python 复核后的难度、
   计划文件数、步数与 token 预算（预估侧），以及实际步数、实际文件数、
   结果状态、完成时间（实际侧）。实际侧由阶段 6 的生成循环结束时回填。

粒度：一行 = 一次规划尝试（同一任务重跑规划会有多行），按 id 取最新。
task_uuid 只存 id，跨库不建外键、不 JOIN（§3.4 铁律）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = 'eac80e932e7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """升级到本版本。"""
    op.create_table(
        'generation_plan',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False, comment='主键 id'),
        sa.Column('plan_uuid', sa.String(length=64), nullable=False, comment='规划唯一标识(uuid4.hex)'),
        sa.Column('user_id', sa.BigInteger(), nullable=False, comment='所属用户 id(MySQL user 表，跨库不 JOIN)'),
        sa.Column('task_uuid', sa.String(length=64), nullable=False, comment='所属生成任务标识(MySQL generation_task.taskUuid)'),
        sa.Column('session_id', sa.BigInteger(), nullable=True, comment='来源会话 id(agent_session.id)；非对话入口发起时为空'),
        sa.Column('final_requirement', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='最终需求(FinalRequirement)'),
        sa.Column('file_plan', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='交付计划(FilePlan)'),
        sa.Column('difficulty', sa.String(length=16), nullable=False, comment='最终难度(Python 复核后，只会上调)'),
        sa.Column('difficulty_declared', sa.String(length=16), nullable=False, comment='模型原始声明的难度(与最终难度对照，用于优化提示词)'),
        sa.Column('planned_file_count', sa.Integer(), nullable=False, comment='计划交付的文件数(清洗后)'),
        sa.Column('budget_steps', sa.Integer(), nullable=False, comment='该难度的步数上限'),
        sa.Column('budget_output_tokens', sa.Integer(), nullable=False, comment='该难度的输出 token 预算'),
        sa.Column('validation_warnings', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='计划清洗阶段的警告(非法文件名、难度上调、悬空依赖等)'),
        sa.Column('actual_steps', sa.Integer(), nullable=True, comment='实际使用的工具调用步数(生成结束后回填)'),
        sa.Column('actual_file_count', sa.Integer(), nullable=True, comment='实际交付的文件数(生成结束后回填)'),
        sa.Column('outcome_status', sa.String(length=16), nullable=True, comment='结果:success/failed(生成结束后回填)'),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True, comment='生成结束时间(生成结束后回填)'),
        sa.Column('create_time', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='创建时间'),
        sa.Column('update_time', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False, comment='更新时间'),
        sa.Column('is_delete', sa.SmallInteger(), server_default=sa.text('0'), nullable=False, comment='是否删除:0否 1是'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plan_uuid'),
    )
    op.create_index('ix_generation_plan_user_id', 'generation_plan', ['user_id'])
    # task_uuid 上的索引是"按任务取最新规划"的依据（同一任务可能重跑规划多次）
    op.create_index('ix_generation_plan_task_uuid', 'generation_plan', ['task_uuid'])


def downgrade() -> None:
    """回滚本版本。"""
    op.drop_index('ix_generation_plan_task_uuid', table_name='generation_plan')
    op.drop_index('ix_generation_plan_user_id', table_name='generation_plan')
    op.drop_table('generation_plan')
