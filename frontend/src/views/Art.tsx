import * as Dialog from "@radix-ui/react-dialog";
import { Check, ImagePlus, Loader2, Paintbrush, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, ArtJob, ArtKind } from "../api";
import { Chip, Panel } from "../ui";

const KIND_LABEL: Record<ArtKind, string> = { character: "portrait", item: "item", mob: "Mob", scene: "scene" };

/** Small "Paint" button that opens the art dialog for one subject. */
export function ArtButton({ cid, kind, subjectId, subjectName, label, onChanged, small = true }:
  { cid: number; kind: ArtKind; subjectId?: number | null; subjectName: string; label?: string; onChanged?: () => void; small?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className={`btn ${small ? "small" : ""} ghost`} title={`Paint ${KIND_LABEL[kind]}: ${subjectName}`}
        onClick={(e) => { e.stopPropagation(); setOpen(true); }}>
        <Paintbrush size={12} />{label !== "" && <> {label ?? "Paint"}</>}
      </button>
      {open && <ArtDialog cid={cid} kind={kind} subjectId={subjectId} subjectName={subjectName} onClose={() => { setOpen(false); onChanged?.(); }} />}
    </>
  );
}

function useJobs(cid: number, params: { kind?: ArtKind; subject_id?: number | null; subject_name?: string }) {
  const [jobs, setJobs] = useState<ArtJob[]>([]);
  const load = useCallback(() => api.artList(cid, params).then(setJobs).catch(() => {}), [cid, params.kind, params.subject_id, params.subject_name]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  const busy = jobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => { if (!busy) return; const t = setInterval(load, 4000); return () => clearInterval(t); }, [busy, load]);
  return { jobs, load, busy };
}

function ArtDialog({ cid, kind, subjectId, subjectName, onClose }:
  { cid: number; kind: ArtKind; subjectId?: number | null; subjectName: string; onClose: () => void }) {
  const params = kind === "mob" ? { kind, subject_name: subjectName } : { kind, subject_id: subjectId ?? undefined };
  const { jobs, load, busy } = useJobs(cid, params);
  const [prompt, setPrompt] = useState("");
  const [look, setLook] = useState("");
  const [savedLook, setSavedLook] = useState("");
  const [open, setOpen] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  useEffect(() => {
    api.artPreview(cid, kind, subjectId, subjectName).then((p) => { setPrompt(p.prompt); setLook(p.appearance ?? ""); setSavedLook(p.appearance ?? ""); setOpen(p.unestablished ?? []); })
      .catch((e) => setErr(String(e)));
    api.artStatus().then(setStatus).catch(() => {});
  }, [cid, kind, subjectId, subjectName]);
  const done = jobs.filter((j) => j.status === "done");
  const current = done.find((j) => j.is_current) ?? done[0];
  const saveLook = async () => {
    if (look === savedLook) return;
    try {
      await api.artAppearance(cid, { kind, subject_id: subjectId ?? undefined, subject_name: subjectName, appearance: look });
      setSavedLook(look);
      setPrompt((await api.artPreview(cid, kind, subjectId, subjectName)).prompt);
    } catch (e) { setErr(String(e)); }
  };
  const render = async () => {
    setErr(null);
    try { await api.artCreate(cid, { kind, subject_id: subjectId ?? undefined, subject_name: subjectName, prompt }); await load(); }
    catch (e) { setErr(String(e)); }
  };
  const act = async (j: ArtJob, action: "use" | "dismiss" | "approve" | "retry") => {
    try { await api.artAction(j.id, action); await load(); } catch (e) { setErr(String(e)); }
  };
  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="sheet art-dialog" aria-describedby={undefined}>
          <div className="row" style={{ marginBottom: 10 }}>
            <Dialog.Title className="h1 grow">{subjectName} <span className="sub">· {KIND_LABEL[kind]}</span></Dialog.Title>
            <Dialog.Close className="btn small ghost" aria-label="Close"><X size={14} /></Dialog.Close>
          </div>
          <div className="art-grid">
            <div className="art-main">
              {current?.url ? <img src={current.url} alt={subjectName} /> : <div className="art-empty muted">No picture yet</div>}
              {busy && <div className="art-busy"><Loader2 size={14} className="spin" /> {jobs.find((j) => j.status === "running") ? "Painting… (about a minute; the first picture after a restart takes a few)" : "Waiting in the queue…"}</div>}
            </div>
            <div className="art-side">
                <div className="h2">Appearance</div>
                <textarea className="input" rows={3} value={look} onChange={(e) => setLook(e.target.value)} onBlur={saveLook}
                  placeholder={kind === "character" ? "e.g. woman in her forties, short grey-streaked hair, broad shoulders, tired eyes" : "what it looks like"} />
                <div className="sub">{open.length ? <>The ledger leaves these open: {open.join(", ").toLowerCase()}. </> : null}
                  Saved for every future picture of {subjectName}, so they stay consistent. Art only; not campaign canon.</div>
              <div className="h2" style={{ marginTop: 10 }}>Prompt</div>
              <textarea className="input" rows={8} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
              <div className="sub">Built from what the party knows. Edit freely; nothing GM-only goes in.</div>
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn" onClick={render} disabled={!prompt.trim()}><ImagePlus size={13} /> Paint</button>
                {status && <span className="sub">{status.running ? <Chip tone="green">ComfyUI ready</Chip> : status.installed ? <Chip>starts on demand</Chip> : <Chip tone="red">ComfyUI not found</Chip>}
                  {status.checkpoint_available === false && <Chip tone="red">model missing</Chip>}</span>}
              </div>
              {err && <div className="error-box" style={{ marginTop: 8 }}>{err}</div>}
              {jobs.filter((j) => j.status === "failed").slice(0, 2).map((j) => (
                <div key={j.id} className="flag warn" style={{ marginTop: 8 }}>Failed: {j.error}
                  <button className="btn small ghost" onClick={() => act(j, "retry")}><RefreshCw size={11} /> Retry</button></div>))}
              {jobs.filter((j) => j.status === "suggested").map((j) => (
                <div key={j.id} className="flag info" style={{ marginTop: 8 }}>GM suggestion{j.reason ? `: ${j.reason}` : ""}
                  <button className="btn small" onClick={() => act(j, "approve")}><Check size={11} /> Paint it</button>
                  <button className="btn small ghost" onClick={() => act(j, "dismiss")}><X size={11} /></button></div>))}
            </div>
          </div>
          {done.length > 1 && (
            <>
              <div className="h2" style={{ marginTop: 12 }}>Gallery <span className="sub">click to use</span></div>
              <div className="art-thumbs">
                {done.map((j) => (
                  <button key={j.id} className={`art-thumb ${j.is_current ? "current" : ""}`} onClick={() => act(j, "use")} title={j.prompt}>
                    <img src={j.url!} alt="" />
                  </button>))}
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** HUD panel: GM picture suggestions waiting for approval, and what is rendering. */
export function ArtQueue({ cid, onChanged }: { cid: number; onChanged?: () => void }) {
  const { jobs, load } = useJobs(cid, {});
  const waiting = jobs.filter((j) => j.status === "suggested");
  const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const prevActive = useState<number>(0);
  useEffect(() => { // refresh the campaign (new portraits, banners) when a render finishes
    if (prevActive[0] > active.length) onChanged?.();
    prevActive[1](active.length);
  }, [active.length]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);
  if (!waiting.length && !active.length) return null;
  return (
    <Panel title={<><Paintbrush size={13} /> Art</>} accent={waiting.length ? `${waiting.length} suggested` : "painting"}>
      {waiting.map((j) => (
        <div key={j.id} className="art-suggest">
          <div><b>{j.subject_name}</b> <span className="sub">· {KIND_LABEL[j.kind]}</span>{j.reason && <div className="sub">{j.reason}</div>}</div>
          <div className="row">
            <button className="btn small" onClick={async () => { await api.artAction(j.id, "approve"); load(); }}><Check size={11} /> Paint</button>
            <button className="btn small ghost" onClick={async () => { await api.artAction(j.id, "dismiss"); load(); }}><X size={11} /></button>
          </div>
        </div>))}
      {active.map((j) => <div key={j.id} className="sub"><Loader2 size={11} className="spin" /> {j.status === "running" ? "Painting" : "Queued"}: {j.subject_name}</div>)}
    </Panel>
  );
}
