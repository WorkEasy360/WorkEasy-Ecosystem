/**
 * "Export CSV" for a list already on screen: the rows the browser holds, as
 * text, saved on this computer. Nothing is sent anywhere. A cell that starts
 * like a formula (=, +, -, @) is prefixed with a quote so a spreadsheet shows
 * it as text instead of running it.
 */
export function toCsv(head: string[], rows: (string | number | null | undefined)[][]): string {
  const cell = (v: string | number | null | undefined) => {
    let text = v === null || v === undefined ? "" : String(v);
    if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [head, ...rows].map((r) => r.map(cell).join(",")).join("\r\n");
}

export function downloadCsv(filename: string, head: string[], rows: (string | number | null | undefined)[][]) {
  const blob = new Blob([`\uFEFF${toCsv(head, rows)}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
