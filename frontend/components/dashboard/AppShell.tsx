"use client";

import {
  BarChart3,
  FileClock,
  FileText,
  LayoutDashboard,
  ReceiptText,
  Sparkles
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const mainItems = [
  { label: "Dashboard", href: "/dashboard", icon: LayoutDashboard },
  { label: "Invoices", href: "/invoices", icon: ReceiptText }
];

const reportItems = [
  { label: "Generate Report", href: "/reports/generate", icon: Sparkles },
  { label: "Report History", href: "/reports/history", icon: FileClock }
];

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50">
      <aside className="fixed inset-y-0 left-0 hidden w-72 border-r border-slate-200 bg-white px-5 py-6 lg:block">
        <div className="mb-9">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-600 text-white">
            <BarChart3 className="h-5 w-5" />
          </div>
          <h1 className="mt-4 text-lg font-semibold tracking-normal text-slate-950">
            ESG Assistant
          </h1>
          <p className="mt-1 text-sm text-slate-500">FYP Demo - 2026</p>
        </div>

        <SidebarSection title="MAIN" items={mainItems} />
        <SidebarSection title="REPORTS" items={reportItems} />
      </aside>

      <main className="lg:pl-72">{children}</main>
    </div>
  );
}

type SidebarItem = {
  label: string;
  href: string;
  icon: typeof LayoutDashboard;
};

function SidebarSection({
  title,
  items
}: {
  title: string;
  items: SidebarItem[];
}) {
  const pathname = usePathname();

  return (
    <nav className="mb-8">
      <p className="mb-3 px-3 text-xs font-semibold uppercase tracking-wider text-slate-400">
        {title}
      </p>
      <div className="space-y-1">
        {items.map((item) => {
          const Icon = item.icon;
          const isActive =
            pathname === item.href || pathname.startsWith(`${item.href}/`);

          return (
            <Link
              key={item.label}
              href={item.href}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${
                isActive
                  ? "bg-blue-50 text-blue-700"
                  : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"
              }`}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
