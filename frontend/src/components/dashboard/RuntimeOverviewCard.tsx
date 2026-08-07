export function RuntimeOverviewCard({title,value,subtitle}:{title:string;value:string|number;subtitle?:string}){
 return <section className="glass-card runtime-overview-card"><small>{title}</small><strong>{value}</strong><span>{subtitle}</span></section>
}
