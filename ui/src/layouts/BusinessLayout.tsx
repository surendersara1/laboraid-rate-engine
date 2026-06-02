import { NavLink, Outlet } from "react-router-dom";

const LINKS = [
  ["/business/inbox", "Inbox"],
  ["/business/by-union", "By Union"],
  ["/business/approved", "Approved"],
  ["/business/rejected", "Rejected"],
  ["/business/queue", "Review Queue"],
  ["/business/me", "My Activity"],
];

export function BusinessLayout(): JSX.Element {
  return (
    <div className="flex min-h-screen">
      <aside className="w-56 bg-brand-dark p-4 text-white">
        <h1 className="mb-6 text-lg font-bold">LaborAid · Business</h1>
        <nav className="space-y-1">
          {LINKS.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `block rounded px-3 py-2 text-sm ${isActive ? "bg-brand" : "hover:bg-brand"}`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  );
}
