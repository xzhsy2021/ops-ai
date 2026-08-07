export default function WorkflowStatus({status}:{status:'running'|'success'|'failed'|'waiting'}){
 const label={running:'执行中',success:'完成',failed:'失败',waiting:'等待'}[status];
 return <span className={`workflow-status ${status}`}>{label}</span>;
}
