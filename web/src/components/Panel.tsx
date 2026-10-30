import type { ReactNode } from "react";
import { num } from "../format";

interface Props {
  title: string;
  source: string;
  n?: number | string | null;
  note?: ReactNode;
  wide?: boolean;
  children: ReactNode;
}

// 패널마다 출처·n 을 작은 글씨로 단다 (#93).
export function Panel({ title, source, n, note, wide, children }: Props) {
  return (
    <section className={wide ? "panel wide" : "panel"}>
      <h2>{title}</h2>
      <div className="panel-body">{children}</div>
      {note && <p className="note">{note}</p>}
      <p className="provenance">
        출처: {source}
        {n != null && <> · n = {typeof n === "number" ? num(n) : n}</>}
      </p>
    </section>
  );
}
