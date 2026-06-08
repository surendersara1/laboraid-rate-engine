import { useEffect, useState } from "react";
import { api } from "../lib/api";

interface UnionMeta {
  slug: string;
  trade: string;
  local: number;
  parent: string;
}

interface ProfileDetail {
  slug: string;
  trade: string;
  local: number;
  parent: string;
  profile_yaml: string | null;
  has_yaml: boolean;
}

export function Profiles(): JSX.Element {
  const [unions, setUnions] = useState<UnionMeta[]>([]);
  const [selected, setSelected] = useState<ProfileDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    api
      .get<{ unions: UnionMeta[] }>("/v1/unions")
      .then((r) => setUnions(r.unions ?? []))
      .catch(() => setUnions([]))
      .finally(() => setLoading(false));
  }, []);

  function open(u: UnionMeta) {
    setDetailLoading(true);
    api
      .get<ProfileDetail>(`/v1/unions/${u.local}/profile`)
      .then(setSelected)
      .catch(() => setSelected(null))
      .finally(() => setDetailLoading(false));
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-2xl font-semibold text-slate-900">Profiles</h2>
        <p className="text-sm text-slate-500">
          The kernel's per-union extraction profile (derived columns, column
          mapping, key fields). Read-only in this view.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* List */}
        <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-100 px-5 py-3">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
              Unions ({unions.length})
            </h3>
          </div>
          {loading ? (
            <p className="p-5 text-sm text-slate-500">Loading…</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {unions.map((u) => (
                <li key={u.slug}>
                  <button
                    type="button"
                    onClick={() => open(u)}
                    className={`flex w-full items-center justify-between px-5 py-3 text-left text-sm hover:bg-slate-50 ${
                      selected?.slug === u.slug ? "bg-amber-50" : ""
                    }`}
                  >
                    <div>
                      <div className="font-medium text-slate-900">
                        {u.trade} {u.local}
                      </div>
                      <div className="font-mono text-xs text-slate-500">{u.slug}</div>
                    </div>
                    <span className="text-xs text-slate-400">{u.parent}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Detail */}
        <div className="rounded-lg border border-slate-200 bg-white shadow-sm lg:col-span-2">
          {!selected && !detailLoading ? (
            <div className="flex h-full items-center justify-center p-12 text-center text-sm text-slate-500">
              Select a union on the left to view its profile.
            </div>
          ) : detailLoading ? (
            <p className="p-5 text-sm text-slate-500">Loading…</p>
          ) : selected ? (
            <div className="flex h-full flex-col">
              <div className="border-b border-slate-100 px-5 py-4">
                <h3 className="text-lg font-semibold text-slate-900">
                  {selected.trade} {selected.local}
                </h3>
                <p className="mt-1 flex gap-3 text-xs text-slate-500">
                  <span>
                    Parent ·{" "}
                    <span className="font-medium text-slate-700">
                      {selected.parent}
                    </span>
                  </span>
                  <span>·</span>
                  <span className="font-mono">{selected.slug}</span>
                  <span>·</span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs ${
                      selected.has_yaml
                        ? "bg-emerald-100 text-emerald-800"
                        : "bg-slate-100 text-slate-700"
                    }`}
                  >
                    {selected.has_yaml ? "profile.yaml loaded" : "no profile.yaml"}
                  </span>
                </p>
              </div>
              <div className="flex-1 overflow-auto p-5">
                {selected.profile_yaml ? (
                  <pre className="rounded-md border border-slate-200 bg-slate-900 p-4 font-mono text-xs leading-relaxed text-slate-100">
                    {selected.profile_yaml}
                  </pre>
                ) : (
                  <p className="text-sm text-slate-500">
                    Profile YAML not bundled in this Lambda deployment yet (v1.1
                    ticket — copy <code className="rounded bg-slate-100 px-1">
                      kernel/profiles/
                    </code>{" "}
                    into the profile-list Lambda asset).
                  </p>
                )}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
