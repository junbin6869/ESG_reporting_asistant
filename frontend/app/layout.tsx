import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ESG Assistant",
  description: "ESG invoice classification and report generation dashboard"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
