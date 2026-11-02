import { useEffect, useState } from "react";
import { api, Campaign, CharacterCard, Ticket } from "../api";
import "../tickets.css";

/** A ticket as a piece of cardstock: punched notches, a perforated stub with its serial, the name, the holder, an EQUIPPED
 * stamp, and uses as punched holes. The text is the rule, shown exactly as the server sends it. */
export function TicketCard({ t, holder, selected, onSelect, large }: { t: Ticket; holder?: string; selected?: boolean; onSelect?: () => void; large?: boolean }) {
  const body = (
    <>
      <div className="tk-main">
        <div className="tk-brand">Carousel</div>
        <div className="tk-name">{t.name}</div>
        {holder && <div className="tk-holder">{holder}</div>}
        {large && <p className="tk-text">{t.text}</p>}
        {t.uses_max != null && (
          <div className="tk-uses" aria-label={`${t.uses_left ?? 0} of ${t.uses_max} uses left`}>
            {Array.from({ length: t.uses_max }, (_, i) => <i key={i} className={i < (t.uses_left ?? 0) ? "full" : "spent"} aria-hidden />)}
            {t.recharge && <span className="tk-recharge">Recharges: {t.recharge}</span>}
          </div>
        )}
        {t.equipped && <span className="tk-stamp">Equipped</span>}
      </div>
      <div className="tk-stub"><span>{t.ref}</span></div>
    </>
  );
  return onSelect
    ? <button className={`ticket ${selected ? "selected" : ""} ${t.equipped ? "equipped" : ""}`} onClick={onSelect} aria-pressed={selected}>{body}</button>
    : <div className={`ticket ${large ? "large" : ""} ${t.equipped ? "equipped" : ""}`}>{body}</div>;
}

type Held = { t: Ticket; who: CharacterCard };

/** The ticket library: every ticket the group holds (the server already leaves hidden tickets out), filtered by holder and
 * equipped state, with the selected one large. Read-only: equipping and using tickets happen in play. */
export function TicketsView({ campaign, version }: { campaign: Campaign; version: number }) {
  const [cards, setCards] = useState<CharacterCard[] | null>(null);
  const [holder, setHolder] = useState<number | "all">("all");
  const [state, setState] = useState<"all" | "equipped" | "carried">("all");
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => { api.characters(campaign.id).then(setCards).catch(() => setCards([])); }, [campaign.id, version]);
  if (!cards) return <div className="view-inner muted">Loading…</div>;
  const party = cards.filter((c) => c.in_party);
  const held: Held[] = party.flatMap((who) => (who.tickets ?? []).map((t) => ({ t, who })));
  const shown = held.filter((h) => (holder === "all" || h.who.id === holder) && (state === "all" || (state === "equipped") === h.t.equipped));
  const key = (h: Held) => `${h.who.id}:${h.t.ref}`;
  const current = shown.find((h) => key(h) === sel) ?? shown[0];
  const first = (n: string) => n.split(" ")[0];
  return (
    <div className="view-inner tickets-view">
      <div className="kicker">The box office</div>
      <h1 className="h1">Tickets to a different ending.</h1>
      {held.length === 0 ? (
        <p className="tk-empty">{campaign.opening?.stage === "car" ? "No tickets yet. Silas hands them out." : "No tickets."}</p>
      ) : (
        <>
          <div className="tk-filters">
            <div className="seg" role="group" aria-label="Holder">
              <button className={holder === "all" ? "active" : ""} aria-pressed={holder === "all"} onClick={() => setHolder("all")}>All</button>
              {party.map((c) => <button key={c.id} className={holder === c.id ? "active" : ""} aria-pressed={holder === c.id} onClick={() => setHolder(c.id)}>{first(c.name)}</button>)}
            </div>
            <div className="seg" role="group" aria-label="Equipped or carried">
              {(["all", "equipped", "carried"] as const).map((s) => (
                <button key={s} className={state === s ? "active" : ""} aria-pressed={state === s} onClick={() => setState(s)}>{s === "all" ? "Any" : s === "equipped" ? "Equipped" : "Carried"}</button>
              ))}
            </div>
          </div>
          <div className="tk-layout">
            <div className="tk-grid">
              {shown.map((h) => <TicketCard key={key(h)} t={h.t} holder={first(h.who.name)} selected={current && key(h) === key(current)} onSelect={() => setSel(key(h))} />)}
              {shown.length === 0 && <p className="muted">No tickets match.</p>}
            </div>
            {current && (
              <aside className="tk-detail" aria-label={`Ticket: ${current.t.name}`}>
                <div className="kicker">Selected ticket</div>
                <TicketCard t={current.t} holder={current.who.name} large />
                <dl className="tk-facts">
                  <dt>Holder</dt><dd>{current.who.name}</dd>
                  <dt>State</dt><dd>{current.t.equipped ? "Equipped" : "Carried"}</dd>
                  {current.t.uses_max != null && <><dt>Uses</dt><dd>{current.t.uses_left ?? 0} of {current.t.uses_max}</dd></>}
                  {current.t.recharge && <><dt>Recharge</dt><dd>{current.t.recharge}</dd></>}
                </dl>
              </aside>
            )}
          </div>
        </>
      )}
    </div>
  );
}
