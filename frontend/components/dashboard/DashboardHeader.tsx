import { CalendarDays } from "lucide-react";

export function DashboardHeader({
  selectedYear,
  years,
  onYearChange
}: {
  selectedYear: string;
  years: string[];
  onYearChange: (year: string) => void;
}) {
  return (
    <header className="flex flex-col gap-4 border-b border-slate-200 bg-white px-5 py-5 sm:flex-row sm:items-center sm:justify-between lg:px-8">
      <div>
        <p className="text-sm font-medium text-slate-500">ESG Assistant</p>
        <h2 className="mt-1 text-2xl font-semibold tracking-normal text-slate-950">
          Dashboard
        </h2>
      </div>

      <label className="inline-flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 shadow-soft">
        <CalendarDays className="h-4 w-4" />
        <select
          className="bg-transparent text-sm font-medium outline-none"
          value={selectedYear}
          onChange={(event) => onYearChange(event.target.value)}
        >
          {years.map((year) => (
            <option key={year} value={year}>
              {year}
            </option>
          ))}
        </select>
      </label>
    </header>
  );
}
