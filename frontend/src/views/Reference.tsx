import { FormEvent, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Check, FileSearch, Search, X } from "lucide-react";
import { api, HouseRule, Ruling } from "../api";
import "../rules.css";
import { Chip, Empty, Markdown } from "../ui";

const LABEL_TONE: Record<string, string> = { BOOK: "cyan", CAMPAIGN: "magenta", INTERPRETATION: "amber", "ONE-SCENE": "amber", OPEN: "red", mixed: "violet" };
const MECH_TONE: Record<string, string> = { implemented: "green", partial: "amber", pending: "red", narrative_only: "" };

const RULING_TONE: Record<string, string> = { precedent: "green", provisional: "amber", rejected: "red" };

/** Opens one rulebook page (from a story citation or a lookup) in the page viewer. */
export function RulePageDialog({ page, onClose }: { page: { doc: number; page: number } | null; onClose: () => void }) {
  const [at, setAt] = useState(page);
  useEffect(() => setAt(page), [page]);
  return (
    <Dialog.Root open={!!at} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="sheet" aria-describedby={undefined}>
          {at && <PageViewer doc={at.doc} page={at.page} onPage={(p) => setAt({ doc: at.doc, page: p })} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function RulesView({ cid }: { cid: number }) {
  const [tab, setTab] = useState<"house" | "rulings" | "books">("rulings");
  const [page, setPage] = useState<{ doc: number; page: number } | null>(null);
  const [house, setHouse] = useState<HouseRule[] | null>(null);
  const [rulings, setRulings] = useState<Ruling[] | null>(null);
  const [docs, setDocs] = useState<Awaited<ReturnType<typeof api.ruleDocs>> | null>(null);

  useEffect(() => { api.houseRules(cid).then(setHouse); api.rulings(cid).then(setRulings); api.ruleDocs().then(setDocs); }, [cid]);
  useEffect(() => {
    if (docs?.index_status?.state !== "running") return;
    const iv = setInterval(() => api.ruleDocs().then(setDocs), 2000);
    return () => clearInterval(iv);
  }, [docs?.index_status?.state]);
  const pending = rulings?.filter((r) => r.status === "provisional" && !r.review).length ?? 0;

  return (
    <div className="view-inner rules">
      <div className="h1">Rules center</div>
      <div className="sub">Precedence: an approved ruling → campaign state → a campaign house rule → the official rulebooks → GM judgment. Source books stay local and are never published.</div>
      <div className="tabs" style={{ marginTop: 12 }} role="tablist">
        {(["house", "rulings", "books"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {{ lookup: "Look up", browse: "Browse", house: `House rules ${house ? `(${house.length})` : ""}`,
               rulings: `Rulings ${rulings ? `(${rulings.length}${pending ? `, ${pending} to review` : ""})` : ""}`, books: `Rulebooks ${docs ? `(${docs.documents.length})` : ""}` }[t]}
          </button>
        ))}
      </div>
      {tab === "house" && (house?.length ? house.map((h) => (
        <details key={h.id} className="card" style={{ marginBottom: 6 }}>
          <summary className="row" style={{ cursor: "pointer" }}>
            <b className="grow">{h.section ? `§${h.section} ` : ""}{h.title}</b>
            {h.label && <Chip tone={LABEL_TONE[h.label]}>{h.label}</Chip>}
            <Chip tone={MECH_TONE[h.mechanical_status]} title="Mechanical implementation status in this app">{h.mechanical_status.replace("_", " ")}</Chip>
          </summary>
          <div style={{ marginTop: 8 }}><Markdown text={h.text} /></div>
          {h.source && <div className="sub">Source: {h.source}</div>}
        </details>
      )) : <Empty>No house rules recorded for this campaign.</Empty>)}
      {tab === "rulings" && <RulingsPanel cid={cid} rulings={rulings} onChanged={(r) => setRulings((all) => all?.map((x) => (x.id === r.id ? r : x)) ?? null)} />}
      {tab === "books" && docs && <BooksPanel docs={docs} onIndex={async (d) => { await api.indexRules(d); setDocs(await api.ruleDocs()); }} />}
      <RulePageDialog page={page} onClose={() => setPage(null)} />
    </div>
  );
}

function RulingsPanel({ cid, rulings, onChanged }: { cid: number; rulings: Ruling[] | null; onChanged: (r: Ruling) => void }) {
  const [only, setOnly] = useState(true);
  if (!rulings) return <div className="muted">Loading…</div>;
  const shown = rulings.filter((r) => !only || (r.status === "provisional" && !r.review));
  const review = async (r: Ruling, d: "approve" | "reject" | "reopen") => onChanged(await api.reviewRuling(cid, r.id, d));
  return (
    <>
      <div className="row" style={{ margin: "10px 0" }}>
        <div className="sub grow">Approved rulings (and your CAMPAIGN or BOOK rulings) are precedent: the GM applies them in later turns. Provisional ones only hold for their scene until you approve them; rejected ones are dropped.</div>
        <label className="sub"><input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} /> only those awaiting review</label>
      </div>
      {shown.length === 0 ? <Empty>{only ? "Nothing awaits review." : "No rulings yet. The GM records them as it adjudicates."}</Empty> : (
        <div className="rulings">{shown.map((r) => (
          <article key={r.id} className={`card ruling ${r.status}`}>
            <div className="row"><b className="grow">{r.topic}</b><Chip tone={LABEL_TONE[r.label]}>{r.label}</Chip><Chip tone={RULING_TONE[r.status]}>{r.status}</Chip></div>
            <div className="rule-text">{r.ruling}</div>
            <div className="row">
              <span className="sub grow">{r.turn_id ? `Turn ${r.turn_id} · ` : ""}{r.created_at.slice(0, 10)}{r.reviewed_at ? ` · reviewed ${r.reviewed_at.slice(0, 10)}` : ""}</span>
              {r.status !== "precedent" && <button className="btn small" onClick={() => review(r, "approve")}><Check size={13} /> Approve as precedent</button>}
              {r.status !== "rejected" && <button className="btn small ghost" onClick={() => review(r, "reject")}>Reject</button>}
              {r.review && <button className="btn small ghost" onClick={() => review(r, "reopen")}>Reopen</button>}
            </div>
          </article>))}
        </div>
      )}
    </>
  );
}

function PageViewer({ doc, page, onPage }: { doc: number; page: number; onPage: (p: number) => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.rulePage>> | null>(null);
  useEffect(() => { setData(null); api.rulePage(doc, page).then(setData); }, [doc, page]);
  return (
    <div className="panel-body">
      <div className="row">
        <Dialog.Title className="h1 grow" style={{ fontSize: 17 }}>{data?.document ?? "…"} — p. {page}</Dialog.Title>
        <button className="btn small" disabled={page <= 1} onClick={() => onPage(page - 1)}>‹ Prev</button>
        <button className="btn small" disabled={!!data && page >= data.page_count} onClick={() => onPage(page + 1)}>Next ›</button>
        <Dialog.Close className="btn ghost small"><X size={16} /></Dialog.Close>
      </div>
      <div className="pre" style={{ marginTop: 12, maxHeight: "none", fontFamily: "var(--body)", fontSize: 14, lineHeight: 1.6 }}>{data ? data.text || "(no extractable text on this page)" : "Loading…"}</div>
    </div>
  );
}

function BooksPanel({ docs, onIndex }: { docs: Awaited<ReturnType<typeof api.ruleDocs>>; onIndex: (d?: string) => Promise<void> }) {
  const [dir, setDir] = useState("");
  const st = docs.index_status ?? {};
  return (
    <>
      {docs.documents.length ? (
        <table className="data"><thead><tr><th>Document</th><th>Pages</th><th>Chunks</th><th>File</th></tr></thead>
          <tbody>{docs.documents.map((d) => <tr key={d.id}><td>{d.title}</td><td className="mono">{d.page_count}</td><td className="mono">{d.chunks}</td><td className="sub">{d.filename}</td></tr>)}</tbody>
        </table>
      ) : <Empty>No rulebooks indexed.</Empty>}
      <div className="h2">Index rulebook PDFs</div>
      <div className="row">
        <input className="input" placeholder="Folder with PDFs (blank = the folder in Settings)" value={dir} onChange={(e) => setDir(e.target.value)} />
        <button className="btn" disabled={st.state === "running"} onClick={() => onIndex(dir || undefined)}><FileSearch size={14} /> {st.state === "running" ? "Indexing…" : "Index"}</button>
      </div>
      {st.state && st.state !== "idle" && <div className="sub" style={{ marginTop: 8 }}>Status: {st.state}{st.error ? ` — ${st.error}` : ""}{st.skipped_duplicates?.length ? ` · skipped duplicate copies: ${st.skipped_duplicates.join(", ")}` : ""}</div>}
    </>
  );
}

export function RecordsView({ cid }: { cid: number }) {
  const [q, setQ] = useState("");
  const [list, setList] = useState<Awaited<ReturnType<typeof api.records>> | null>(null);
  const [open, setOpen] = useState<Awaited<ReturnType<typeof api.record>> | null>(null);
  useEffect(() => { api.records(cid).then(setList); }, [cid]);
  const search = async (e: FormEvent) => { e.preventDefault(); setList(await api.records(cid, q || undefined)); };
  const byDoc = (list ?? []).reduce<Record<string, typeof list>>((acc, r) => { (acc[r!.document_path] ??= [] as any)!.push(r); return acc; }, {});
  return (
    <div className="view-inner">
      <div className="h1">Campaign records</div>
      <div className="sub">The imported ledger, verbatim and searchable (player-visible files only). History without a structured table yet lives here and is available to the GM.</div>
      <form className="row" style={{ margin: "12px 0" }} onSubmit={search}>
        <Search size={16} color="var(--muted)" /><input className="input" placeholder="Search records (e.g. Margit, Murder Drum, Floor 2 warden)…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn">Search</button>
      </form>
      {!list ? <div className="muted">Loading…</div> : list.length === 0 ? <Empty>No records{q ? " match" : " imported for this campaign"}.</Empty> : (
        Object.entries(byDoc).map(([doc, items]) => (
          <details key={doc} className="card" style={{ marginBottom: 6 }} open={!!q}>
            <summary style={{ cursor: "pointer" }}><b>{doc}</b> <span className="muted">{items!.length} sections</span></summary>
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              {items!.map((r) => <li key={r.id} style={{ marginBottom: 4 }}><a href="#" onClick={async (e) => { e.preventDefault(); setOpen(await api.record(cid, r.id)); }}>{r.section}</a>
                {r.snippet && <div className="sub">{r.snippet.split(/\[|\]/).map((s, i) => (i % 2 ? <mark key={i}>{s}</mark> : s))}</div>}</li>)}
            </ul>
          </details>
        ))
      )}
      <Dialog.Root open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="sheet" aria-describedby={undefined}>
            {open && (
              <div className="panel-body">
                <div className="row"><Dialog.Title className="h1 grow" style={{ fontSize: 16 }}>{open.document_path} · {open.section}</Dialog.Title><Dialog.Close className="btn ghost small"><X size={16} /></Dialog.Close></div>
                {open.document_path.endsWith(".md") ? <div style={{ marginTop: 12 }}><Markdown text={open.content} /></div> : <div className="pre" style={{ marginTop: 12, maxHeight: "none" }}>{open.content}</div>}
              </div>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
