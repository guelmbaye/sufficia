import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/AppShell";
import { RunProvider } from "@/lib/run";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "SUFFICIA", template: "%s · SUFFICIA" },
  description: "Evidence-bound execution for autonomous agents: an agent may spend only when the evidence its principal required is established.",
};

export const viewport: Viewport = { themeColor: "#ffffff", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <RunProvider>
          <AppShell>{children}</AppShell>
        </RunProvider>
      </body>
    </html>
  );
}
