export default function EmptyState({message="No data"}:{message?:string}){
 return <div className="glass-panel empty-state">{message}</div>;
}
