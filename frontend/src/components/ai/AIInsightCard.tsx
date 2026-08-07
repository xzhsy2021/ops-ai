
export interface AIInsightCardProps {
  title?: string;
  message: string;
  actionLabel?: string;
  onAction?: () => void;
}

export default function AIInsightCard({title='AI 寤鸿', message, actionLabel='鏌ョ湅', onAction}: AIInsightCardProps){
  return <section className="glass-panel ai-insight-card">
    <div className="ai-insight-title">{title}</div>
    <div className="ai-insight-message">{message}</div>
    {onAction && <button onClick={onAction}>{actionLabel}</button>}
  </section>;
}
