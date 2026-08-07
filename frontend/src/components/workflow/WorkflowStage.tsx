export function WorkflowStage({name,status}:{name:string;status:string}){
 return <div className={`workflow-stage status-${status}`}><b>{name}</b><span>{status}</span></div>
}
