// Visible placeholder for POC pages still being wired to the API.
export function ComingSoon({
  title,
  description,
  icon = "🚧",
}: {
  title: string;
  description?: string;
  icon?: string;
}): JSX.Element {
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-semibold text-slate-900">{title}</h2>
      <div className="rounded-lg border border-slate-200 bg-white p-12 text-center shadow-sm">
        <div className="mx-auto mb-4 text-5xl">{icon}</div>
        <p className="font-medium text-slate-700">{description ?? "Coming in v1.1"}</p>
        <p className="mt-2 text-sm text-slate-500">
          The data is already in Aurora — this view is next on the build list.
        </p>
      </div>
    </div>
  );
}
