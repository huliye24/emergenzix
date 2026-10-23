"use client";

export type WorkspaceView =
  | "overview"
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
  index: string;
  label: string;
  description: string;
}[] = [
  { id: "overview", index: "00", label: "Overview", description: "Workspace" },
  { id: "strategies", index: "01", label: "Strategies", description: "Definitions" },
  { id: "backtests", index: "02", label: "Backtests", description: "Evaluation" },
  { id: "paper", index: "03", label: "Paper trading", description: "Simulation" },
  { id: "activity", index: "04", label: "Activity", description: "Order history" },
];

export default function WorkspaceNavigation({
  activeView,
  onNavigate,
}: WorkspaceNavigationProps) {
  return (
    <nav className="workspace-nav" aria-label="Workspace sections">
      <div className="workspace-nav-heading">
        <span>WORKSPACE</span>
        <small>RESEARCH · VALIDATION · OPERATIONS</small>
      </div>
      <div className="workspace-nav-items">
        {NAV_ITEMS.map((item) => {
          const active = activeView === item.id;
          return (
            <button
              key={item.id}
              type="button"
              className={"workspace-nav-item" + (active ? " is-active" : "")}
              onClick={() => onNavigate(item.id)}
              aria-current={active ? "page" : undefined}
            >
              <span className="workspace-nav-index">{item.index}</span>
              <span className="workspace-nav-copy">
                <strong>{item.label}</strong>
                <small>{item.description}</small>
              </span>
              <span className="workspace-nav-arrow" aria-hidden="true">↗</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
