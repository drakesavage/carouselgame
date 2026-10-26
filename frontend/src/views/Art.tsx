import * as Dialog from "@radix-ui/react-dialog";
import { Check, Crop, ImagePlus, Loader2, Paintbrush, RefreshCw, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ArtJob, ArtKind } from "../api";
import { Chip, Panel, Portrait, PortraitCrop, sized } from "../ui";

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
  const [drafting, setDrafting] = useState(false);
  const [engine, setEngine] = useState<string>(kind === "character" ? "codex" : "comfy");
  const [refs, setRefs] = useState<{ name: string; url: string }[]>([]);
  const refInput = useRef<HTMLInputElement>(null);
  useEffect(() => { if (kind === "character" && subjectId != null) api.artRefs(cid, subjectId).then(setRefs).catch(() => {}); }, [cid, kind, subjectId]);
  const [err, setErr] = useState<string | null>(null);
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  useEffect(() => {
    api.artPreview(cid, kind, subjectId, subjectName, engine).then((p) => { setPrompt(p.prompt); if (p.engine) setEngine(p.engine); setLook(p.appearance ?? ""); setSavedLook(p.appearance ?? ""); setOpen(p.unestablished ?? []); })
      .catch((e) => setErr(String(e)));
    api.artStatus().then(setStatus).catch(() => {});
  }, [cid, kind, subjectId, subjectName]); // eslint-disable-line react-hooks/exhaustive-deps
  const switchEngine = async (e: string) => {
    setEngine(e);
    try { setPrompt((await api.artPreview(cid, kind, subjectId, subjectName, e)).prompt); } catch (x) { setErr(String(x)); }
  };
  const done = jobs.filter((j) => j.status === "done");
  const current = done.find((j) => j.is_current) ?? done[0];
  const saveLook = async () => {
    if (look === savedLook) return;
    try {
      await api.artAppearance(cid, { kind, subject_id: subjectId ?? undefined, subject_name: subjectName, appearance: look });
      setSavedLook(look);
      setPrompt((await api.artPreview(cid, kind, subjectId, subjectName, engine)).prompt);
    } catch (e) { setErr(String(e)); }
  };
  const render = async () => {
    setErr(null);
    try { await api.artCreate(cid, { kind, subject_id: subjectId ?? undefined, subject_name: subjectName, prompt, engine }); await load(); }
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
              {current?.url ? <img src={sized(current.url, 512)!} alt={subjectName} /> : <div className="art-empty muted">No picture yet</div>}
              {busy && <div className="art-busy"><Loader2 size={14} className="spin" /> {jobs.find((j) => j.status === "running") ? "Painting… (about a minute; the first picture after a restart takes a few)" : "Waiting in the queue…"}</div>}
            </div>
            <div className="art-side">
                {kind === "character" && (
                  <>
                    <div className="h2">Painter</div>
                    <div className="row">
                      <select className="select input" value={engine} onChange={(e) => switchEngine(e.target.value)}>
                        <option value="codex">Codex image tool (best likeness; uses your ChatGPT image allowance)</option>
                        <option value="comfy">ComfyUI on this PC (fast, unlimited)</option>
                      </select>
                    </div>
                    {engine === "codex" && subjectId != null && (
                      <>
                        <div className="h2" style={{ marginTop: 10 }}>Reference photos <span className="sub">(optional; the face follows them)</span></div>
                        <div className="art-thumbs">
                          {refs.map((r) => (
                            <div key={r.name} className="art-thumb ref"><img src={sized(r.url, 128)!} alt="" loading="lazy" />
                              <button className="btn small ghost" title="Remove" onClick={async () => setRefs(await api.artRefDelete(cid, subjectId, r.name))}><X size={10} /></button></div>))}
                          {refs.length < 4 && <button className="art-thumb add" onClick={() => refInput.current?.click()}><ImagePlus size={18} /></button>}
                        </div>
                        <input ref={refInput} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={async (e) => {
                          const f = e.target.files?.[0]; if (!f) return;
                          try { setRefs(await api.artRefUpload(cid, subjectId, f)); setPrompt((await api.artPreview(cid, kind, subjectId, subjectName, engine)).prompt); }
                          catch (x) { setErr(String(x)); } finally { e.target.value = ""; }
                        }} />
                        <div className="sub">Photos are sent to OpenAI with the request. Stored only on this PC (datart).</div>
                      </>
                    )}
                  </>
                )}
                <div className="h2" style={{ marginTop: 10 }}>Appearance</div>
                <textarea className="input" rows={3} value={look} onChange={(e) => setLook(e.target.value)} onBlur={saveLook}
                  placeholder={kind === "character" ? "e.g. woman in her forties, short grey-streaked hair, broad shoulders, tired eyes" : "what it looks like"} />
                {(kind === "character" || kind === "item") && subjectId != null && (
                  <button className="btn small ghost" style={{ justifySelf: "start", marginTop: 4 }} disabled={drafting} onClick={async () => {
                    setDrafting(true); setErr(null);
                    try {
                      if (kind === "character") setLook((await api.artDraftAppearance(cid, subjectId)).appearance);
                      else {  // items: the draft is saved like the batch on the Inventory page, so refresh the prompt too
                        const d = (await api.artDraftItemLooks(cid, { item_ids: [subjectId], overwrite: true })).drafted[0];
                        if (d) { setLook(d.appearance); setSavedLook(d.appearance); setPrompt((await api.artPreview(cid, kind, subjectId, subjectName, engine)).prompt); }
                      }
                    } catch (e) { setErr(String(e)); } finally { setDrafting(false); }
                  }}>{drafting ? <Loader2 size={11} className="spin" /> : <Sparkles size={11} />} Draft with Gemini</button>)}
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
                    <img src={sized(j.url, 128)!} alt="" loading="lazy" />
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

/** Adjust which square of a portrait shows in every portrait tile: drag the square, zoom with the slider. */
export function CropButton({ cid, c, onSaved, compact }: { cid: number; c: { id: number; name: string; portrait_path?: string | null; portrait_crop?: PortraitCrop | null }; onSaved: () => void; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="btn small ghost" style={compact ? undefined : { marginTop: 4, width: "100%" }} onClick={() => setOpen(true)} title="Adjust portrait crop"><Crop size={12} /> {compact ? "Crop" : "Adjust crop"}</button>
      {open && c.portrait_path && <CropDialog cid={cid} c={c} url={`media/portraits/${encodeURIComponent(c.portrait_path)}`} onClose={(saved) => { setOpen(false); if (saved) onSaved(); }} />}
    </>
  );
}

const CROP_W = 360;

function CropDialog({ cid, c, url, onClose }: { cid: number; c: { id: number; name: string; portrait_crop?: PortraitCrop | null }; url: string; onClose: (saved: boolean) => void }) {
  const [ar, setAr] = useState<number | null>(null);
  const [crop, setCrop] = useState<{ cx: number; cy: number; k: number } | null>(c.portrait_crop ?? null);
  const drag = useRef<{ x: number; y: number; cx: number; cy: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const img = new Image();
    img.onload = () => {
      const a = img.naturalWidth / img.naturalHeight;
      setAr(a);
      // start from the saved crop, else the default framing (a full-width square near the top of tall pictures)
      setCrop((cur) => cur ?? { cx: 0.5, cy: a < 1 ? Math.min(0.5, a / 2 + 0.12 * (1 - a)) : 0.5, k: 1 });
    };
    img.src = url;
  }, [url]);
  if (ar == null || !crop) return null;
  const dispW = CROP_W, dispH = CROP_W / ar;
  const side = crop.k * Math.min(dispW, dispH);
  const clamp = (v: number, half: number, full: number) => Math.min(1 - half / full, Math.max(half / full, v));
  const set = (cx: number, cy: number, k: number) => {
    const s = k * Math.min(dispW, dispH);
    setCrop({ k, cx: clamp(cx, s / 2, dispW), cy: clamp(cy, s / 2, dispH) });
  };
  const save = async (value: PortraitCrop | null) => {
    try { await api.portraitCrop(cid, c.id, value); onClose(true); } catch (e) { setErr(String(e)); }
  };
  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose(false)}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="modal crop-dialog" aria-describedby={undefined}>
          <Dialog.Title className="h2">Portrait crop: {c.name}</Dialog.Title>
          <div className="crop-stage" style={{ width: dispW, height: dispH, backgroundImage: `url("${url}")` }}
            onPointerMove={(e) => { const d = drag.current; if (d) set(d.cx + (e.clientX - d.x) / dispW, d.cy + (e.clientY - d.y) / dispH, crop.k); }}
            onPointerUp={() => { drag.current = null; }} onPointerLeave={() => { drag.current = null; }}>
            <div className="crop-box" style={{ width: side, height: side, left: crop.cx * dispW - side / 2, top: crop.cy * dispH - side / 2 }}
              onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture?.(e.pointerId); drag.current = { x: e.clientX, y: e.clientY, cx: crop.cx, cy: crop.cy }; }} />
          </div>
          <div className="row" style={{ marginTop: 10, gap: 12 }}>
            <label className="grow">Zoom
              <input type="range" min={0.2} max={1} step={0.01} value={1.2 - crop.k} onChange={(e) => set(crop.cx, crop.cy, 1.2 - Number(e.target.value))} style={{ width: "100%" }} />
            </label>
            <Portrait name={c.name} src={url} crop={{ ...crop, ar }} size={72} />
          </div>
          {err && <div className="error-box">{err}</div>}
          <div className="row" style={{ marginTop: 10, justifyContent: "flex-end" }}>
            <button className="btn small ghost" onClick={() => save(null)}>Reset</button>
            <button className="btn small ghost" onClick={() => onClose(false)}>Cancel</button>
            <button className="btn small" onClick={() => save({ ...crop, ar })}><Check size={12} /> Save</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
