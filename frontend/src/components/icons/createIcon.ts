import { defineComponent, h, type VNode } from "vue";

export type IconNode = readonly [
  tag: string,
  attrs: Record<string, string | number>,
  children?: readonly IconNode[],
];

function renderNodes(nodes: readonly IconNode[]): VNode[] {
  return nodes.map(([tag, attrs, children]) =>
    h(tag, attrs, children && children.length > 0 ? renderNodes(children) : undefined),
  );
}

export interface IconOptions {
  viewBox?: string;
  defaultStrokeWidth?: number | string;
}

/**
 * 基于 IconMCP (https://iconmcp.com) 16×16 网格矢量数据生成轻量 Vue 图标组件。
 * 兼容 size / strokeWidth / color 与常规 class / aria 属性透传。
 */
export function createIcon(
  displayName: string,
  nodes: readonly IconNode[],
  options: IconOptions = {},
) {
  const viewBox = options.viewBox ?? "0 0 16 16";
  const baseStroke = options.defaultStrokeWidth ?? 1.05;

  return defineComponent({
    name: displayName,
    inheritAttrs: true,
    props: {
      size: {
        type: [Number, String],
        default: 24,
      },
      color: {
        type: String,
        default: "currentColor",
      },
      strokeWidth: {
        type: [Number, String],
        default: undefined,
      },
    },
    setup(props, { attrs }) {
      return () =>
        h(
          "svg",
          {
            xmlns: "http://www.w3.org/2000/svg",
            width: props.size,
            height: props.size,
            viewBox,
            fill: "none",
            stroke: props.color,
            "stroke-width": props.strokeWidth ?? baseStroke,
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
            "aria-hidden": attrs["aria-label"] ? undefined : "true",
            ...attrs,
          },
          renderNodes(nodes),
        );
    },
  });
}
