/** ArcPicker 的共享类型（供主组件与选项子组件使用）。 */

export type ArcPickerSide = "top" | "bottom" | "left" | "right";

export interface ArcPickerOption {
  value: string;
  label: string;
  disabled?: boolean;
}
