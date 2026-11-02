"use client";

import AppShell from "@/components/AppShell";
import McpSection from "@/components/mcp/McpSection";

export default function McpPage() {
  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-4xl px-6 py-8">
          <McpSection />
        </div>
      </div>
    </AppShell>
  );
}
