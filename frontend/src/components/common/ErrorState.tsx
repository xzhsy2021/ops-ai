export default function ErrorState({message="Request failed"}:{message?:string}){
 return <div className="glass-panel error-state">{message}</div>;
}
