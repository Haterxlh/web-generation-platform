import Button from '@/components/common/Button'
import type { RequirementSlots } from '@/types/agent_types'
import { describeSlots } from './generate_utils'
import styles from './RequirementCard.module.css'

interface RequirementCardProps {
  slots: RequirementSlots | null
  /** 需求是否已足够开始生成（与后端 REQUIRED_SLOTS 同口径） */
  ready: boolean
  /** 后端还缺哪些槽位 */
  missingSlots: string[]
  /** 生成中：禁用"开始生成"，避免重复提交 */
  busy: boolean
  onStart: () => void
}

/** 槽位名 → 中文（后端返回的 missing_slots 是字段名，要翻译给用户看） */
const SLOT_TEXT: Record<string, string> = {
  site_kind: '站点类型',
  features: '核心功能',
  audience: '目标用户',
  style: '风格',
  need_persistence: '是否需要数据存储',
}

/** 把槽位值渲染成人能读的文案；null / 空数组都返回 null（调用方据此显示"未提及"） */
function renderValue(value: string | string[] | boolean | null): string | null {
  if (value === null) return null
  if (Array.isArray(value)) return value.length === 0 ? null : value.join('、')
  if (typeof value === 'boolean') return value ? '需要' : '不需要'
  return value.trim() === '' ? null : value
}

/** 一行槽位；值缺失时显示"未提及"，而不是把这一行藏掉 */
function SlotRow({ label, value }: { label: string; value: string | null }) {
  return (
    <div className={styles.slot}>
      <span className={styles.slotLabel}>{label}</span>
      <span className={value === null ? `${styles.slotValue} ${styles.slotEmpty}` : styles.slotValue}>
        {value ?? '未提及'}
      </span>
    </div>
  )
}

/**
 * 需求确认卡片：把当前累计的槽位摊开，让用户在点"开始生成"之前看到**即将被生成的东西**。
 *
 * 为什么必须显式展示：会话式流程里需求是多轮拼起来的，
 * 用户很容易以为"我说过的都算数"。把槽位（含"未提及"）摆出来，
 * 需求跑偏的那一刻就发生在用户眼前，而不是等到预览打开才发现。
 *
 * 三态提醒：`need_persistence` 为 null 表示"用户还没提过"，**不能显示成"不需要"**。
 */
export default function RequirementCard({
  slots,
  ready,
  missingSlots,
  busy,
  onStart,
}: RequirementCardProps) {
  const rows =
    slots === null
      ? []
      : [
          { label: SLOT_TEXT.site_kind, value: renderValue(slots.site_kind) },
          { label: SLOT_TEXT.features, value: renderValue(slots.features) },
          { label: SLOT_TEXT.audience, value: renderValue(slots.audience) },
          { label: SLOT_TEXT.style, value: renderValue(slots.style) },
          { label: SLOT_TEXT.need_persistence, value: renderValue(slots.need_persistence) },
        ]

  return (
    <section className={styles.card}>
      <h2 className={styles.title}>需求确认</h2>
      <p className={styles.subtitle}>
        {ready
          ? describeSlots(slots) || '需求已经清楚了'
          : '信息还不够，继续聊几句；补齐下面的必备信息后就能生成'}
      </p>

      <div className={styles.slots}>
        {rows.map((row) => (
          <SlotRow key={row.label} label={row.label} value={row.value} />
        ))}
      </div>

      {!ready && missingSlots.length > 0 && (
        <p className={styles.missing}>
          还缺：{missingSlots.map((name) => SLOT_TEXT[name] ?? name).join('、')}
        </p>
      )}

      <div className={styles.actions}>
        <Button disabled={!ready || busy} onClick={onStart}>
          {busy ? '正在生成…' : '开始生成'}
        </Button>
        <span className={styles.note}>
          生成走 Agent 流水线（需求归并 → 规划 → 工具调用生成 → 完成门禁），
          产物会自动保存到「我的项目」。
        </span>
      </div>
    </section>
  )
}
