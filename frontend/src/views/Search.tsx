import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Backpack, BookOpen, Clock3, FileText, Flag, Footprints, Loader2, MessageSquareText, Scroll, Search, UserRound, X } from "lucide-react";
import { api, SearchHit } from "../api";
import "../journal.css";

const TYPE: Record<SearchHit["type"], { label: string; icon: typeof Flag }> = {
  quest: { label: "Lead", icon: Flag }, person: { label: "Person", icon: UserRound }, group: { label: "Group", icon: Footprints },
  lore: { label: "Clue", icon: BookOpen }, history: { label: "History", icon: Scroll }, item: { label: "Item", icon: Backpack },
  event: { label: "Event", icon: Clock3 }, story: { label: "Story", icon: MessageSquareText }, record: { label: "Record", icon: FileText },
};

/** In-game search over what the party knows: story, people, journal, lore, items, events and records. Never GM text (server-side). */
export function SearchDialog({ cid, open, onClose, go }: { cid: number; open: boolean; onClose: () => void; go: (h: SearchHit) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  useEffect(() => {
    if (q.trim().length < 2) { setHits(null); return; }
    const n = ++seq.current;
    setBusy(true);
    const t = setTimeout(() => api.search(cid, q.trim()).then((h) => { if (n === seq.current) setHits(h); })
      .catch(() => { if (n === seq.current) setHits([]); }).finally(() => { if (n === seq.current) setBusy(false); }), 250);
    return () => clearTimeout(t);
  }, [q, cid]);
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="search-dialog" aria-describedby={undefined}>
          <Dialog.Title className="sr-only">Search the campaign</Dialog.Title>
          <div className="search-bar">
            {busy ? <Loader2 size={16} className="spin" /> : <Search size={16} />}
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search what the group knows: people, places, clues, stories…" aria-label="Search the campaign" />
            <Dialog.Close className="btn ghost small" aria-label="Close search"><X size={14} /></Dialog.Close>
          </div>
          <div className="search-results" role="listbox" aria-label="Results">
            {hits == null ? <div className="sub search-hint">Try "Scout promise", "Marcus family", "prison recon" or an item name.</div>
              : hits.length === 0 ? <div className="sub search-hint">Nothing the party knows matches.</div>
              : hits.map((h, i) => {
                const T = TYPE[h.type];
                return (
                  <button key={`${h.type}-${h.id ?? h.key}-${i}`} role="option" aria-selected={false} className="search-hit" onClick={() => { go(h); onClose(); }}>
                    <T.icon size={15} className="sh-icon" />
                    <span className="sh-main">
                      <span className="sh-title">{h.title}{h.status && h.status !== "active" ? <span className="muted"> · {h.status}</span> : null}</span>
                      <span className="sh-snippet">{h.snippet}</span>
                    </span>
                    <span className="sh-type">{T.label}</span>
                  </button>
                );
              })}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
