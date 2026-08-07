
export type StatusType = "success"|"running"|"warning"|"error"|"idle";

export default function StatusBadge({status}:{status:StatusType|string}){
 return <span className={`status-badge status-${status}`}>{status}</span>;
}
