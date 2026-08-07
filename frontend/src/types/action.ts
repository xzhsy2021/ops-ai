export interface AIAction {
  type: "recommend" | "execute" | "verify";
  label: string;
  risk?: "low" | "medium" | "high";
}
