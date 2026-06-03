// 403 view shown when a signed-in user lacks the Cognito group for an area
// (Spec/09 §4 L1 §1.1 — `/admin/*` returns 403 for Business users). Replaces the
// previous silent redirect so the denial is explicit (audit D4).
export function Forbidden403(): JSX.Element {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-2 p-8 text-center">
      <h1 className="text-3xl font-semibold text-red-600">403 — Forbidden</h1>
      <p className="text-gray-600">
        Your account does not have access to this area.
      </p>
    </div>
  );
}
