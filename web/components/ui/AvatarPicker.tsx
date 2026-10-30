"use client";

import { useRef, useState } from "react";
import { ImagePlus, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { toast } from "@/lib/ui";
import Avatar from "./Avatar";
import { AVATAR_PICKER_OPTIONS } from "./avatarIcons";

/** 头像选择：SVG 图标快捷选择 + 上传图片。 */
export default function AvatarPicker({
  value,
  onChange,
  size = 44,
}: {
  value: string;
  onChange: (v: string) => void;
  size?: number;
}) {
  const file = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const upload = async (f?: File | null) => {
    if (!f) return;
    if (f.size > 2 * 1024 * 1024) return toast("图片不能超过 2 MB", "error");
    setBusy(true);
    try {
      const r = await api.uploadImage(f);
      onChange(r.url);
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
      if (file.current) file.current.value = "";
    }
  };

  return (
    <div className="flex items-start gap-4">
      <div className="relative mr-1 shrink-0 pr-1">
        <Avatar value={value} size={size} />
        <button
          type="button"
          onClick={() => file.current?.click()}
          disabled={busy}
          title="上传图片"
          className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full border border-line bg-panel text-faint shadow-sm hover:text-ink"
        >
          {busy ? <Loader2 size={10} className="animate-spin" /> : <ImagePlus size={10} />}
        </button>
        <input
          ref={file}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          className="hidden"
          onChange={(e) => upload(e.target.files?.[0])}
        />
      </div>

      <div className="min-w-0 flex-1 pl-0.5">
        <div className="flex flex-wrap gap-1">
          {AVATAR_PICKER_OPTIONS.map(({ key, label, Icon }) => (
            <button
              key={key}
              type="button"
              title={label}
              onClick={() => onChange(key)}
              className={`flex h-7 w-7 items-center justify-center rounded-md border transition-colors ${
                value === key
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-transparent text-muted hover:border-line hover:bg-panel2 hover:text-ink"
              }`}
            >
              <Icon size={15} />
            </button>
          ))}
        </div>
        <p className="mt-1 text-[10.5px] text-faint">
          点右上角上传图片（PNG/JPG，≤2MB），也可以选一个图标
        </p>
      </div>
    </div>
  );
}
