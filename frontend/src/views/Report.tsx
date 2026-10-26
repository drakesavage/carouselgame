import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Bug, Check, Copy, Loader2, X } from "lucide-react";
import { api } from "../api";

/** "Something's wrong with this turn": saves a redacted report (no GM secrets) to data/reports and offers it to copy. */
export function ReportButton({ cid, turnId, compact = true }: { cid: number; turnId: number | null; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ path: string; markdown: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const send = async () => {
    setBusy(true);
    try { setDone(await api.problemReport(cid, turnId, note)); } finally { setBusy(false); }
  };
  return (
    <>
      <button className={`btn ${compact ? "small ghost report-btn" : "small"}`} onClick={() => { setOpen(true); setDone(null); setCopied(false); }}
        title="Report a problem with this turn (saves a redacted report)"><Bug size={12} />{compact ? null : " Report a problem"}</button>
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="modal" aria-describedby={undefined}>
            <div className="row"><Dialog.Title className="h1 grow" style={{ fontSize: 17 }}>Report a problem{turnId ? ` · turn ${turnId}` : ""}</Dialog.Title>
              <Dialog.Close className="btn ghost small" aria-label="Close"><X size={14} /></Dialog.Close></div>
            {!done ? (
              <>
                <p className="sub">What went wrong? The report adds this turn's player-visible exchange, its timing, the tools the GM used, token use and any integrity flags, and the current state. GM secrets, the GM's context and tool details are left out.</p>
                <textarea className="input" rows={4} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. My lantern vanished after the shop visit" autoFocus />
                <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}>
                  <button className="btn primary" disabled={busy} onClick={send}>{busy ? <Loader2 size={13} className="spin" /> : <Bug size={13} />} Save report</button>
                </div>
              </>
            ) : (
              <>
                <p className="sub">Saved to <span className="mono">{done.path}</span>.</p>
                <pre className="pre" style={{ maxHeight: 280, overflow: "auto", fontSize: 12 }}>{done.markdown}</pre>
                <div className="row" style={{ justifyContent: "flex-end" }}>
                  <button className="btn" onClick={async () => { try { await navigator.clipboard.writeText(done.markdown); setCopied(true); } catch { /* clipboard unavailable */ } }}>
                    {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? "Copied" : "Copy report"}</button>
                </div>
              </>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
