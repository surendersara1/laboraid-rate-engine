export function Dashboard(): JSX.Element {
  const tiles = [
    ["Jobs in flight", "—"],
    ["Failed (24h)", "—"],
    ["P95 latency", "—"],
    ["Bedrock spend (7d)", "—"],
    ["Error budget", "—"],
    ["Alarms", "OK"],
  ];
  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold">Dashboard</h2>
      <div className="grid grid-cols-3 gap-4">
        {tiles.map(([label, value]) => (
          <div key={label} className="rounded-lg border bg-white p-4">
            <p className="text-sm text-slate-500">{label}</p>
            <p className="text-2xl font-bold">{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
