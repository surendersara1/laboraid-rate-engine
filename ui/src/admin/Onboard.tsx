import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";

// Move 5 — New-Union Onboarding Workflow.
//
// Per the client integration brief (docs/Design/client_brief_and_integration_plan.md)
// + MASTER_DATA_REVIEW_RULES.md, a union cannot be turned on for automated rate-sheet
// production until an admin confirms each of the twelve master-data review rules can
// be satisfied for that local. Until every box is checked, the "Enable union" CTA is
// disabled. State is persisted to localStorage for the POC; production will move this
// to DDB so a partial checklist survives logout.

interface RuleItem {
  id: string;
  title: string;
  why: string;
}

const RULES: RuleItem[] = [
  {
    id: "rule-1",
    title: "Rule 1 — Scope using master ID scheme (F<local>… vs F000…)",
    why: "Confirm the local has its own F<local>NNN fund rows in Master Fund List and that shared F000NNN funds are correctly cross-referenced.",
  },
  {
    id: "rule-2",
    title: "Rule 2 — Every fund column header resolves to a Master Fund Name",
    why: "Document labels vary; every name on the sheet must map to a master `Fund Name` (drift handled via Rule 10).",
  },
  {
    id: "rule-3",
    title: "Rule 3 — Document lines can map many-to-one to master funds",
    why: "Several rate-notice lines may roll up into one master fund row — confirm the mapping is documented for this local.",
  },
  {
    id: "rule-4",
    title: "Rule 4 — `Fund Type` (Contribution vs Deduction) agrees with doc framing",
    why: "Deductions render gray-filled and reduce net pay; misclassification mis-renders the sheet and corrupts remit calcs.",
  },
  {
    id: "rule-5",
    title: "Rule 5 — `Percentage Based Fund` governs $ vs % formatting",
    why: "Hourly/Percent/Both flag drives cell number-format; wrong flag misrepresents the contribution math.",
  },
  {
    id: "rule-6",
    title: "Rule 6 — Package names resolve to Master Package List",
    why: "Apprentice classes, journeyman, foreman variants must map to P000NNN; missing packages block extraction.",
  },
  {
    id: "rule-7",
    title: "Rule 7 — Zone names resolve to Master Zone List",
    why: "Each zone string the CBA uses must map to a Z000NNN row, either union-specific or generic 'All'.",
  },
  {
    id: "rule-8",
    title: "Rule 8 — Indenture-date variants & duplicate rows are anticipated",
    why: "Some locals split apprentices by indenture date — confirm the duplicate-row pattern is acceptable.",
  },
  {
    id: "rule-9",
    title: "Rule 9 — Values come from documents, never from master sheets",
    why: "Master lists carry NO rates. Confirm the rate notice / CBA / wage sheet for this local has the dollar values.",
  },
  {
    id: "rule-10",
    title: "Rule 10 — Drift / NOT_FOUND dispositions have an explicit owner",
    why: "Each mismatch is fix-sheet / reconcile / add-master-row / update-master. Confirm the admin owning this triage for this local.",
  },
  {
    id: "rule-11",
    title: "Rule 11 — Trustee/address block validates remittance identity",
    why: "Confirm the union's trustee block in Master Trustee list is current (out of scope for extraction but required before publish).",
  },
  {
    id: "rule-12",
    title: "Rule 12 — Conditional-fund applicability notes captured",
    why: "Some funds only apply under certain conditions; flag any conditional contribution rules in this local's CBA.",
  },
];

export function Onboard(): JSX.Element {
  const { local = "" } = useParams<{ local: string }>();
  const storageKey = `laboraid:onboard:${local}`;

  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (raw) {
        const parsed = JSON.parse(raw);
        setChecked(parsed.checked ?? {});
        setNotes(parsed.notes ?? {});
      }
    } catch {
      /* localStorage off — checklist still works, just won't persist */
    }
    setHydrated(true);
  }, [storageKey]);

  useEffect(() => {
    if (!hydrated) return;
    try {
      window.localStorage.setItem(
        storageKey,
        JSON.stringify({ checked, notes, updated_at: new Date().toISOString() })
      );
    } catch {
      /* same as above */
    }
  }, [checked, notes, hydrated, storageKey]);

  const completed = useMemo(
    () => RULES.filter((r) => checked[r.id]).length,
    [checked]
  );
  const allComplete = completed === RULES.length;

  const enable = () => {
    if (!allComplete) return;
    // POC: log to console + show banner. Production wires this to
    // POST /v1/unions/{local}/enable which flips a flag in DDB.
    // eslint-disable-next-line no-console
    console.info(
      `[onboard] enabling local=${local} with completed checklist:`,
      Object.keys(checked).filter((k) => checked[k])
    );
    window.alert(
      `Union local ${local} marked ready for automated rate-sheet production.\n\n` +
        `12/12 master-data review rules confirmed by admin.\n\n` +
        `(POC: this call is logged client-side; production will POST to /v1/unions/${local}/enable.)`
    );
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">
          Onboard Union — Local {local || "(missing)"}
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          Confirm each of the twelve master-data review rules can be satisfied for
          this local before enabling automated rate-sheet production. Until every box
          is checked, the union cannot be enabled.
        </p>
      </header>

      <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm font-medium text-slate-700">
            Checklist progress
          </span>
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${
              allComplete
                ? "bg-emerald-100 text-emerald-800 ring-emerald-200"
                : "bg-amber-100 text-amber-800 ring-amber-200"
            }`}
          >
            {completed} / {RULES.length}
          </span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
          <div
            className={`h-full transition-all ${
              allComplete ? "bg-emerald-500" : "bg-amber-400"
            }`}
            style={{ width: `${(completed / RULES.length) * 100}%` }}
          />
        </div>
      </div>

      <ol className="space-y-3">
        {RULES.map((r) => {
          const on = !!checked[r.id];
          return (
            <li
              key={r.id}
              className={`rounded-lg border p-4 shadow-sm transition ${
                on
                  ? "border-emerald-200 bg-emerald-50/40"
                  : "border-slate-200 bg-white"
              }`}
            >
              <label className="flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                  checked={on}
                  onChange={(e) =>
                    setChecked((c) => ({ ...c, [r.id]: e.target.checked }))
                  }
                />
                <div className="flex-1">
                  <p className="text-sm font-semibold text-slate-900">{r.title}</p>
                  <p className="mt-0.5 text-xs text-slate-600">{r.why}</p>
                  <textarea
                    value={notes[r.id] ?? ""}
                    onChange={(e) =>
                      setNotes((n) => ({ ...n, [r.id]: e.target.value }))
                    }
                    placeholder="Notes / link to source doc (optional)"
                    rows={1}
                    className="mt-2 w-full rounded-md border border-slate-200 px-2 py-1 text-xs focus:border-emerald-400 focus:outline-none focus:ring-1 focus:ring-emerald-300"
                  />
                </div>
              </label>
            </li>
          );
        })}
      </ol>

      <div className="sticky bottom-4 flex items-center justify-end gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-md">
        <span className="text-xs text-slate-500">
          {allComplete
            ? "All twelve rules confirmed."
            : `${RULES.length - completed} item(s) remaining.`}
        </span>
        <button
          type="button"
          disabled={!allComplete}
          onClick={enable}
          className="rounded-md bg-emerald-600 px-5 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          title={
            allComplete
              ? `Enable local ${local} for automated rate-sheet production`
              : "Complete all 12 master-data review rules to enable this union"
          }
        >
          Enable union {local}
        </button>
      </div>
    </div>
  );
}
