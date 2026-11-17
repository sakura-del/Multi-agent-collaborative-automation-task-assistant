import type { Component } from "vue";

export interface SidebarNavItem {
  name: string;
  label: string;
  icon: Component;
  adminOnly?: boolean;
}

export interface SidebarNavGroup {
  label: string;
  icon: Component;
  items: SidebarNavItem[];
}
