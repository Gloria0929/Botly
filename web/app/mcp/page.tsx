"use client";

import AppShell from "@/components/AppShell";
import McpSection from "@/components/mcp/McpSection";

export default function McpPage() {
  return (
    <AppShell>
      <div className="h-full overflow-y-auto">
        <div className="page">
          <McpSection />
        </div>
      </div>
    </AppShell>
  );
}
