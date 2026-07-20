import { Building2, Factory, ShieldCheck, UsersRound } from "lucide-react";
import type { DashboardSummary, EsgCategory } from "@/lib/api";

const cards: Array<{
  key: "total" | EsgCategory;
  title: string;
  icon: typeof Building2;
  color: string;
}> = [
  {
    key: "total",
    title: "Total invoices",
    icon: Building2,
    color: "bg-slate-100 text-slate-700"
  },
  {
    key: "Environmental",
    title: "Environmental",
    icon: Factory,
    color: "bg-emerald-50 text-emerald-700"
  },
  {
    key: "Social",
    title: "Social",
    icon: UsersRound,
    color: "bg-blue-50 text-blue-700"
  },
  {
    key: "Governance",
    title: "Governance",
    icon: ShieldCheck,
    color: "bg-violet-50 text-violet-700"
  }
];

export function SummaryCards({ summary }: { summary: DashboardSummary }) {
  return (
    <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      {cards.map((card) => {
        const Icon = card.icon;
        const value =
          card.key === "total"
            ? summary.total_invoices
            : summary.categories[card.key];

        return (
          <div
            key={card.key}
            className="rounded-lg border border-slate-200 bg-white p-5 shadow-soft"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-slate-500">
                  {card.title}
                </p>
                <p className="mt-3 text-3xl font-semibold tracking-normal text-slate-950">
                  {value.count}
                </p>
              </div>
              <div
                className={`flex h-10 w-10 items-center justify-center rounded-lg ${card.color}`}
              >
                <Icon className="h-5 w-5" />
              </div>
            </div>
            <p className="mt-4 text-sm text-slate-500">
              {formatSummaryAmount(value)}
            </p>
          </div>
        );
      })}
    </section>
  );
}

function formatSummaryAmount(value: {
  amount: number;
  currency: string;
  amounts_by_currency?: Record<string, number>;
}) {
  const amounts = Object.entries(value.amounts_by_currency ?? {});
  if (amounts.length === 0) return formatCurrency(value.amount, value.currency);
  return amounts
    .map(([currency, amount]) => formatCurrency(amount, currency))
    .join(" · ");
}

function formatCurrency(amount: number, currency: string) {
  return new Intl.NumberFormat("en-MY", {
    style: "currency",
    currency,
    maximumFractionDigits: 2
  }).format(amount);
}
