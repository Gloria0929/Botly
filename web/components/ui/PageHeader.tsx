import React from "react";

/**
 * 页面通用标题区：所有一级页面共用同一套字号、间距与操作区对齐方式，
 * 避免各页自己写 flex / text-[21px] 导致左右边界和基线不一致。
 */
export default function PageHeader({
  title,
  description,
  actions,
  badge,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  badge?: React.ReactNode;
  eyebrow?: string;
}) {
  return (
    <header className="page-header">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="page-title">{title}</h1>
            {badge}
          </div>
          {description && <p className="page-desc">{description}</p>}
        </div>
        {actions && (
          <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
            {actions}
          </div>
        )}
      </div>
    </header>
  );
}
