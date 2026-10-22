import { FormEvent, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { BookOpen, FileSearch, Gavel, Search, X } from "lucide-react";
import { api, HouseRule, Passage } from "../api";
import { Chip, Empty, Markdown } from "../ui";

const LABEL_TONE: Record<string, string> = { BOOK: "cyan", CAMPAIGN: "magenta", INTERPRETATION: "amber", "ONE-SCENE": "amber", OPEN: "red", mixed: "violet" };
const MECH_TONE: Record<string, string> = { implemented: "green", partial: "amber", pending: "red", narrative_only: "" };

function highlight(text: string, q: string) {
  const terms = q.toLowerCase().split(/\W+/).filter((t) => t.length > 2);
  if (!terms.length) return text;
  const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part));
}

export function RulesView({ cid }: { cid: number }) {
  const [tab, setTab] = useState<"search" | "house" | "rulings" | "books">("search");
  const [q, setQ] = useState("");
  const [res, setRes] = useState<{ campaign: (HouseRule & { score: number })[]; official: Passage[] } | null>(null);
  const [page, setPage] = useState<{ doc: number; page: number } | null>(null);
  const [house, setHouse] = useState<HouseRule[] | null>(null);
  const [rulings, setRulings] = useState<Awaited<ReturnType<typeof api.rulings>> | null>(null);
  const [docs, setDocs] = useState<Awaited<ReturnType<typeof api.ruleDocs>> | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.houseRules(cid).then(setHouse); api.rulings(cid).then(setRulings); api.ruleDocs().then(setDocs); }, [cid]);
  useEffect(() => {
    if (docs?.index_status?.state !== "running") return;
    const iv = setInterval(() => api.ruleDocs().then(setDocs), 2000);
    return () => clearInterval(iv);
  }, [docs?.index_status?.state]);

  const search = async (e?: FormEvent) => {
    e?.preventDefault();
    if (q.trim().length < 2) return;
    setBusy(true);
    try { setRes(await api.ruleSearch(q, cid)); } finally { setBusy(false); }
  };

  return (
    <div className="view-inner">
      <div className="h1">Rules reference</div>
      <div className="sub">Precedence: your explicit ruling → campaign state → campaign house rules → official rulebooks → GM judgment. Source books stay local and are never published.</div>
      <div className="tabs" style={{ marginTop: 12 }}>
        {(["search", "house", "rulings", "books"] as const).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {{ search: "Search", house: `House rules ${house ? `(${house.length})` : ""}`, rulings: `Rulings ${rulings ? `(${rulings.length})` : ""}`, books: `Rulebooks ${docs ? `(${docs.documents.length})` : ""}` }[t]}
          </button>
        ))}
      </div>

      {tab === "search" && (
        <>
          <form className="row" onSubmit={search}>
            <Search size={16} color="var(--muted)" />
            <input className="input" autoFocus placeholder="e.g. grapple, advantage on help, crafting failure, healing potion…" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className="btn primary" disabled={busy}>Search</button>
          </form>
          {res && (
            <>
              <div className="h2"><Gavel size={14} /> Campaign house rules</div>
              {res.campaign.length ? res.campaign.map((h) => (
                <div key={h.id} className="card" style={{ marginBottom: 8 }}>
                  <div className="row"><b className="grow">{h.section ? `§${h.section} ` : ""}{h.title}</b>{h.label && <Chip tone={LABEL_TONE[h.label]}>{h.label}</Chip>}</div>
                  <div style={{ fontSize: 13.5, marginTop: 6, whiteSpace: "pre-wrap" }}>{highlight(h.text.replace(/\*\*/g, ""), q)}</div>
                </div>
              )) : <div className="muted">No matching house rules.</div>}
              <div className="h2"><BookOpen size={14} /> Official rulebooks</div>
              {res.official.length ? res.official.map((p) => (
                <div key={p.chunk_id} className="card" style={{ marginBottom: 8 }}>
                  <div className="row"><Chip tone="cyan">{p.ref}</Chip><span className="spacer" /><button className="btn small ghost" onClick={() => setPage({ doc: p.document_id, page: p.page })}>Open page</button></div>
                  <div style={{ fontSize: 13.5, marginTop: 6, whiteSpace: "pre-wrap" }}>{highlight(p.text.slice(0, 900), q)}{p.text.length > 900 ? "…" : ""}</div>
                </div>
              )) : <div className="muted">{docs?.documents.length ? "No passages matched — try other words." : "No rulebooks indexed yet (see the Rulebooks tab)."}</div>}
            </>
          )}
        </>
      )}

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

      {tab === "rulings" && (rulings?.length ? (
        <table className="data"><thead><tr><th>Topic</th><th>Ruling</th><th>Label</th></tr></thead>
          <tbody>{rulings.map((r) => <tr key={r.id}><td>{r.topic}</td><td>{r.ruling}</td><td><Chip tone={LABEL_TONE[r.label]}>{r.label}</Chip> <Chip>{r.certainty}</Chip></td></tr>)}</tbody>
        </table>
      ) : <Empty>No rulings yet. The GM records them as it adjudicates.</Empty>)}

      {tab === "books" && docs && <BooksPanel docs={docs} onIndex={async (d) => { await api.indexRules(d); setDocs(await api.ruleDocs()); }} />}

      <Dialog.Root open={!!page} onOpenChange={(o) => !o && setPage(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="sheet" aria-describedby={undefined}>
            {page && <PageViewer doc={page.doc} page={page.page} onPage={(p) => setPage({ doc: page.doc, page: p })} />}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
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
