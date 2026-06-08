import { NavLink, Outlet } from "react-router-dom";
import { logout } from "../lib/auth";

const LINKS: Array<[string, string, string]> = [
  ["/business/inbox", "Inbox", "📥"],
  ["/business/by-union", "By Union", "🏷️"],
  ["/business/approved", "Approved", "✅"],
  ["/business/rejected", "Rejected", "🚫"],
  ["/business/queue", "Review Queue", "🗂️"],
  ["/business/me", "My Activity", "👤"],
];

export function BusinessLayout(): JSX.Element {
  return (
    <div className="flex min-h-screen bg-slate-50">
      <aside className="flex w-60 flex-col border-r border-slate-200 bg-slate-900 text-slate-200">
        <div className="border-b border-slate-800 px-5 py-5">
          <div className="text-xs font-medium uppercase tracking-wider text-slate-500">
            LaborAid
          </div>
          <div className="mt-1 text-lg font-semibold text-white">Business</div>
        </div>
        <nav className="flex-1 space-y-1 px-3 py-4">
          {LINKS.map(([to, label, icon]) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition ${
                  isActive
                    ? "bg-slate-800 text-white"
                    : "text-slate-300 hover:bg-slate-800 hover:text-white"
                }`
              }
            >
              <span className="text-base">{icon}</span>
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-slate-800 px-3 py-3">
          <button
            type="button"
            onClick={() => void logout()}
            className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm text-slate-300 transition hover:bg-slate-800 hover:text-white"
          >
            <span>↩</span>
            <span>Sign out</span>
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-auto">
        <div className="mx-auto max-w-7xl px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
