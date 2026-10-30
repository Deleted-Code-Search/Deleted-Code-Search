import { MARK_CLOSE, MARK_OPEN } from "../format";

// API snippet 의 \u0002 … \u0003 구간을 <mark> 로. 문자열을 HTML 로 넣지 않는다.
export function Highlight({ text }: { text: string }) {
  const parts: { text: string; hit: boolean }[] = [];
  for (const piece of text.split(MARK_OPEN)) {
    const close = piece.indexOf(MARK_CLOSE);
    if (close === -1) {
      parts.push({ text: piece, hit: false });
    } else {
      parts.push({ text: piece.slice(0, close), hit: true });
      parts.push({ text: piece.slice(close + 1), hit: false });
    }
  }
  return (
    <>
      {parts.map((part, i) =>
        part.hit ? <mark key={i}>{part.text}</mark> : <span key={i}>{part.text}</span>,
      )}
    </>
  );
}
