"use client";

import { useState, type ReactNode } from "react";

export type WorkspaceView =
  | "overview"
  | "market"
  | "strategies"
  | "backtests"
  | "paper"
  | "activity";

interface WorkspaceNavigationProps {
  activeView: WorkspaceView;
  onNavigate: (view: WorkspaceView) => void;
}

const NAV_ITEMS: {
  id: WorkspaceView;
  label: string;
  description: string;
  icon: ReactNode;
}[] = [
  {
    id: "overview",
    label: "实盘",
    description: "LIVE MONITOR",
    icon: <><rect x="3" y="5" width="18" height="14" rx="3" /><path d="M15 10h6v4h-6a2 2 0 0 1 0-4Z" /></>,
  },
  {
    id: "market",
    label: "市场观测",
    description: "PREDICT / SIGNAL",
    icon: <><path d="M3 17l5-5 4 3 7-8" /><path d="M15 7h4v4" /><path d="M3 21h18" /></>,
  },
  {
    id: "backtests",
    label: "回测",
    description: "BACKTEST",
    icon: <><path d="M9 3h6M10 3v5l-5 9a3 3 0 0 0 2.6 4.5h8.8A3 3 0 0 0 19 17l-5-9V3" /><path d="M8 14h8" /></>,
  },
  {
    id: "paper",
    label: "影子跟随",
    description: "SHADOW PAPER",
    icon: <><path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="17" r="2" /></>,
  },
];

export default function WorkspaceNavigation({ activeView, onNavigate }: WorkspaceNavigationProps) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside className={"workspace-nav" + (collapsed ? " is-collapsed" : "")} aria-label="交易工作台">
      <div className="workspace-nav-brand">
        <span className="workspace-brand-mark">E</span>
        <span className="workspace-brand-copy"><strong>emergenzix</strong><small>QUANT SYSTEM</small></span>
        <button type="button" className="workspace-collapse" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? "展开导航" : "收起导航"} title={collapsed ? "展开导航" : "收起导航"}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d={collapsed ? "m9 18 6-6-6-6" : "m15 18-6-6 6-6"} /></svg>
        </button>
      </div>

      <div className="workspace-nav-heading"><span>WORKSPACE</span></div>
      <nav className="workspace-nav-items" aria-label="主要模式">
        {NAV_ITEMS.map((item) => {
          const active = activeView === item.id;
          return (
            <button type="button" key={item.id} className={"workspace-nav-item" + (active ? " is-active" : "")} onClick={() => onNavigate(item.id)} aria-current={active ? "page" : undefined} title={collapsed ? item.label : undefined}>
              <span className="workspace-nav-icon"><svg viewBox="0 0 24 24" aria-hidden="true">{item.icon}</svg></span>
              <span className="workspace-nav-copy"><strong>{item.label}</strong><small>{item.description}</small></span>
              <span className="workspace-nav-arrow" aria-hidden="true">›</span>
            </button>
          );
        })}
      </nav>

      <div className="workspace-nav-footer">
        <span className="status-dot" />
        <span className="workspace-nav-copy"><strong>Cloud ready</strong><small>VIEW ONLY</small></span>
      </div>
    </aside>
  );
}
