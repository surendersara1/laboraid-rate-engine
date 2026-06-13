import { NavLink, Outlet } from "react-router-dom";
import { logout } from "../lib/auth";

const LINKS: Array<[string, string]> = [
  ["/admin/dashboard", "Dashboard"],
  ["/admin/uploads", "Uploads"],
  ["/admin/jobs", "Jobs"],
  ["/admin/agents", "Agents"],
  ["/admin/profiles", "Profiles"],
  ["/admin/audit", "Audit"],
  ["/admin/costs", "Costs"],
];

export function AdminLayout(): JSX.Element {
  return (
    <div className="flex min-h-screen bg-slate-50">
      <aside className="flex w-60 flex-col bg-brand text-slate-200">
        <div className="flex items-center gap-3 border-b border-white/10 px-5 py-5">
          <img
            src="/laboraid-shield.png"
            alt="LaborAid"
            className="h-9 w-9 shrink-0 drop-shadow"
          />
          <div>
            <div className="text-base font-bold leading-tight text-white">LaborAid</div>
            <div className="text-xs font-medium uppercase tracking-wider text-gold">
              Admin Console
            </div>
          </div>
        </div>
        <nav className="flex-1 space-y-1 px-3 py-4">
          {LINKS.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `block rounded-md border-l-2 px-3 py-2 text-sm transition ${
                  isActive
                    ? "border-gold bg-white/10 font-semibold text-white"
                    : "border-transparent text-slate-300 hover:bg-white/5 hover:text-white"
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-white/10 px-3 py-3">
          <button
            type="button"
            onClick={() => void logout()}
            className="block w-full rounded-md px-3 py-2 text-left text-sm text-slate-300 transition hover:bg-white/5 hover:text-white"
          >
            Sign out
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
