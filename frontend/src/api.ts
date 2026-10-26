// Typed client for the player API. The frontend never calls /api/gm: GM-only data is not part of this bundle.

export type Stats = { stats?: Record<string, number>; modifiers?: Record<string, number>; unenhanced?: Record<string, number> };
export type Status = { id: number; name: string; description?: string | null; polarity: string; expires_at_minutes?: number | null };
export type CharacterCard = {
  id: number; slug: string; name: string; kind: string; roster_category?: string | null; crawler_number?: string | null; species?: string | null; race?: string | null;
  class_name?: string | null; level?: number | null; status: string; location?: string | null; in_party: number; party_order: number;
  hp_current?: number | null; hp_max?: number | null; mana_current?: number | null; mana_max?: number | null; ai_favor?: number | null;
  portrait_crop?: { cx: number; cy: number; k: number; ar: number } | null;
  stats: Stats; derived: Record<string, unknown>; portrait_path?: string | null; statuses: Status[]; equipped: { id: number; name: string; slot?: string }[];
  personality?: string | null; known_motivation?: string | null; attitude?: number | null; voice?: string | null; assignment?: string | null;
  life?: Life;
};
/** Whether someone is around at all, apart from combat HP (a deceased character's HP is the last record). */
export type Life = { state: "active" | "deceased" | "missing" | "retired" | "contact"; label: string; note: string | null };
export type Person = CharacterCard & {
  life: Life; allegiance: string | null; relationship_notes: string[]; where: string | null;
  group: { label: string; location: string | null; activity: string | null } | null;
  commitments: { id: number; kind: string; title: string }[];
  last_interaction: { text: string; game_time_minutes: number | null; source: "memory" | "event" | "profile" } | null;
};
export type Skill = { id: number; kind: string; name: string; rank: number | null; mana_cost: number | null; passive: number; marked: number; description?: string | null; properties: Record<string, unknown> };
export type Item = { id: number; owner_character_id: number | null; owner_name?: string | null; name: string; quantity: number | null; equipped: number; deployed?: number; slot?: string | null; rarity?: string | null; description?: string | null; properties: Record<string, unknown> };
/** Inventory view of an item (memory/inventory.py): category, honest quantity, invention stage. */
export type InvItem = Item & { category: string; quantity_state: "exact" | "unknown"; quantity_note: string | null; status: string | null;
  invention: boolean; stage: Stage | null; storage: string | null; attached_to: string | null };
export type Stage = "planned" | "designed" | "in_progress" | "finished" | "installed";
export type Inventory = {
  shared: InvItem[]; by_character: InvItem[];
  gold: { amount: number | null; item_id: number | null; committed: number; history: { id: number; summary: string; delta: number | null; game_time_minutes: number | null }[] };
  reservations: { quest_id: number; item: string; payment: string | null; paid_gold: number | null; held_by: string | null; claim: string | null; owned: boolean; note: string | null }[];
  projects: { quest_id: number; title: string; stage: Stage; status_text: string | null; build_time: string | null; difficulty: number | null; lead: string | null;
    assistant: string | null; linked_items: { id: number; name: string; owner: string | null }[]; materials: { material: string; need: number; have: number | null; enough: boolean }[] }[];
  materials: { id: number; name: string; quantity: number | null; owner: string | null }[];
};
export type CharacterSheet = CharacterCard & {
  bio?: string | null; sheet: Record<string, unknown>; skills: Skill[]; items: Item[];
  achievements: { title: string; description?: string; game_time_minutes?: number }[];
  npc_profile: { personality?: string; voice?: string; known_motivation?: string; attitude?: number | null } | null;
  relationships: { kind: string; description?: string; a_name: string; b_name: string }[];
  limited_uses: { ability: string; per: string; uses: number; uses_allowed: number; cooldown_minutes: number | null }[];
};
export type Quest = { id: number; kind: string; title: string; description?: string | null; status: string; priority: number; progress?: string | null; giver?: string | null;
  details?: Record<string, unknown>; due_at_minutes?: number | null; immediate?: boolean };  // immediate: part of the linked immediate objective
export type Scene = { id: number; title: string; location: string | null; group_key: string | null; situation: string | null; conditions: string | null;
  threats: string | null; participants: string[]; started_game_minutes: number | null };
export type Group = { key: string; label: string; location: string | null; activity: string | null; members: CharacterCard[] };
export type DayInfo = { day: number; minutes_into_day: number; minutes_to_reset: number; day_length: number };
export type FloorClock = { elapsed_minutes: number; deadline_minutes: number | null; remaining_minutes: number | null; remaining_display: string | null };
export type Clock = { key: string; label: string; kind: string; current: number; maximum: number | null; unit?: string | null };
export type Campaign = {
  id: number; name: string; is_demo: number; data_source: string; current_floor: number | null; current_location: string | null; safe_room: number;
  game_time_minutes: number; floor_deadline_minutes: number | null; active_character_id: number | null; active_objective: string | null;
  floor_clock: FloorClock; party: CharacterCard[]; quests: Quest[]; clocks: Clock[];
  encounter: Encounter | null;
  last_save: { id: number; label: string; kind: string; created_at: string } | null;
  scene: Scene | null; groups: Group[]; roster: CharacterCard[]; day: DayInfo;
  recent_events: { id: number; type: string; summary: string; game_time_minutes: number | null }[];
  art?: ArtMap;
};
export type ArtKind = "character" | "item" | "mob" | "scene" | "location";
/** Current picture URL per subject: ids as keys, except mobs (lower-case name). */
export type ArtMap = Record<ArtKind, Record<string, string>>;
export type ArtJob = { id: number; campaign_id: number; kind: ArtKind; subject_id: number | null; subject_name: string; prompt: string;
  width: number; height: number; seed: number; status: "suggested" | "queued" | "running" | "done" | "failed" | "dismissed";
  source: "player" | "gm"; reason: string | null; note: string | null; error: string | null; is_current: number; url: string | null;
  created_at: string; finished_at: string | null; checkpoint: string | null; engine?: string };
export type Combatant = { id: string; name: string; side: "party" | "foe"; defeated?: boolean; hb_percent?: number; actions_left?: number;
  evading?: boolean; hp_current?: number | null; hp_max?: number | null; dead?: boolean; dying?: string | null };
export type Encounter = { id: number; name: string; round: number; phase?: string; summary?: string | null; combatants: Combatant[] };
export type CampaignListItem = { id: number; name: string; is_demo: number; data_source: string; current_floor: number | null; current_location: string | null };
export type RollCard = { roll_id: number; expression: string; dice: { sides: number; value: number; kept: boolean }[]; total: number; target: number | null;
  outcome: string | null; degree: string | null; reason: string | null; detail: Record<string, any> };
export type Message = { id: number; turn_id: number | null; role: "player" | "gm" | "system"; content: string; created_at: string;
  meta: { rolls?: RollCard[]; changes?: string[]; rules_cited?: string[]; provider?: string; floor_clock?: FloorClock; style?: string;
    cards?: { type: string; summary: string }[] } };
/** The quest journal (memory/journal.py): canonical quests, promises, reservations and projects with their people and places. */
export type JournalQuest = { id: number; kind: string; status: string; priority: number; title: string; description: string | null; progress: string | null;
  giver: string | null; details: Record<string, unknown>; immediate: boolean; holder: string | null; people: { id: number; name: string }[];
  places: string[]; due_in_minutes: number | null; depends_on: unknown; source: string | null; updated_at: string };
export type LoreFact = { id: number; subject: string; fact: string; certainty: string; source: string | null; category: string; topic: string | null; character_id: number | null };
export type Chronology = { day_length: number; days: { day: number | null; events: (EventRow & { bookkeeping: boolean })[] }[];
  imported_history: { id: number; fact: string; source: string | null }[] };
export type SearchHit = { type: "person" | "quest" | "group" | "lore" | "history" | "item" | "event" | "story" | "record"; id?: number; key?: string;
  title: string; snippet: string; score: number; status?: string; kind?: string; owner?: string | null; role?: string; document?: string };
/** Rules center (reference/rulecenter.py). */
export type Ruling = { id: number; turn_id: number | null; topic: string; ruling: string; certainty: string; label: string; created_at: string;
  review: "approved" | "rejected" | null; review_note: string | null; reviewed_at: string | null; status: "precedent" | "provisional" | "rejected" };
export type Coverage = { status: string; evidence: string; gap: string; priority?: string };
export type RuleLookup = {
  query: string;
  book: { name: string; ref: string; document_id: number; page: number; chapter: string | null; excerpt: string }[];
  house_rules: (HouseRule & { score: number; coverage: Coverage })[];
  rulings: Ruling[];
  implementation: { status: string; evidence: string | null; gap: string; source: string | null };
  precedence: { layer: "ruling" | "house_rule" | "book" | "gm" | "note"; says: string }[];
};
export type RuleSuggestion = { kind: "entry" | "house_rule"; label: string; ref?: string; id?: number; section?: string | null };
export type RuleBrowse = { books?: { id: number; title: string; entry_count: number; chapters: number }[];
  book?: { id: number; title: string; page_offset: number } | null; chapters?: { title: string; page: number; sections: { title: string; page: number }[] }[];
  entries?: { name: string; page: number; chapter: string | null }[] };
/** The places the party knows (memory/places.py): a nested tree from recorded locations, no invented geography. */
export type Place = { key: string; name: string; parent: string | null; children: string[]; visited: boolean; saferoom: boolean; current: boolean;
  floor: number | null; description: string | null; groups: { key: string; label: string; members: number }[]; sources: string[] };
export type KnownWorld = { floor: number | null; roots: string[]; nodes: Record<string, Place>; routes: { a: string; b: string; times: number; last: number | null }[];
  unplaced_groups: { key: string; label: string }[] };
export type Manifest = { locations: Record<string, string>; characters: Record<string, string> };
export type DiagnosticsRow = { turn_id: number; status: string; input: string; created_at: string; rounds: number; flags: number; input_tokens: number };
export type TurnStatus = { turn_id: number; status: "processing" | "completed" | "failed"; replayed: boolean; error: string | null;
  result: { narration: string; rolls: RollCard[]; changes: string[]; notices: { text: string; style: string }[] } | null;
  progress: { stage: string; detail: string; elapsed_s: number } | null };
export type EventRow = { id: number; type: string; summary: string; game_time_minutes: number | null; created_at: string };
export type HouseRule = { id: number; section: string | null; title: string; text: string; label: string | null; status: string; mechanical_status: string; source: string | null };
export type Passage = { chunk_id: number; document_id: number; document: string; page: number; ref: string; text: string; score: number };
export type SavePoint = { id: number; label: string; kind: string; turn_id: number | null; created_at: string };
export type Prefs = { gm_provider: string; codex_model: string; gemini_model: string; gemini_key_present?: boolean; context_profile: string; gemini_effort?: string; gemini_context_profile?: string; codex_reasoning_effort: string; rulebook_dir: string; ledger_dir: string;
  active_campaign_id: number | null; codex_cli_path: string | null; providers: string[]; efforts: string[]; data_dir: string };

export type LedgerChange = { id: string; file: string; summary: string; group: string; status: "clean" | "conflict"; visibility: string; kind: string;
  detail: { from?: unknown; to?: unknown; conflict?: string; ledger_now?: unknown } };
export type LedgerPreview = {
  ledger_path: string; base_commit: string; fingerprint: string; suggested_message: string; warnings: string[]; blockers: string[];
  status: { branch: string; head: string; dirty_files: string[]; ahead: number | null; behind: number | null; upstream: string | null; fetch_error: string | null;
    author_default: { name: string; email: string } };
  changes: LedgerChange[]; files: { path: string; visibility: string; new_file: boolean; diff: string }[];
  last_sync: { new_commit: string; created_at: string } | null;
  checkpoint_curated: boolean | null;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}), ...init?.headers } });
  if (res.status === 401) {
    // Another device without the passcode cookie (access through Barcode Battles): go to the passcode page.
    window.location.href = "login";
    throw new ApiError(401, "passcode required");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail
        // validation errors: "prompt: String should have at most 4000 characters" rather than the raw list
        : Array.isArray(j.detail) ? j.detail.map((e: { loc?: unknown[]; msg?: string }) => `${String(e.loc?.[e.loc.length - 1] ?? "request")}: ${e.msg}`).join("; ")
        : JSON.stringify(j.detail ?? j);
    } catch { /* not json */ }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}
const post = <T,>(path: string, body?: unknown) => req<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => req<{ provider: string; codex_cli: string | null; rulebook_index: boolean }>("api/health"),
  campaigns: () => req<CampaignListItem[]>("api/campaigns"),
  campaign: (id: number) => req<Campaign>(`api/campaigns/${id}`),
  characters: (id: number) => req<CharacterCard[]>(`api/campaigns/${id}/characters`),
  character: (id: number, cid: number) => req<CharacterSheet>(`api/campaigns/${id}/characters/${cid}`),
  dossier: (id: number, cid: number) => req<Record<string, unknown>>(`api/campaigns/${id}/characters/${cid}/dossier`),
  explainAbilities: (id: number) => req<{ state: string; progress?: string; error?: string }>(`api/campaigns/${id}/abilities/explain`, { method: "POST" }),
  explainStatus: (id: number) => req<{ state: string; progress?: string; error?: string }>(`api/campaigns/${id}/abilities/explain`),
  inventory: (id: number) => req<Inventory>(`api/campaigns/${id}/inventory`),
  quests: (id: number) => req<Quest[]>(`api/campaigns/${id}/quests`),
  npcs: (id: number) => req<Person[]>(`api/campaigns/${id}/npcs`),
  timeline: (id: number) => req<EventRow[]>(`api/campaigns/${id}/timeline`),
  backups: () => req<{ backups: { name: string; label: string; created: string | null; size_mb: number; has_manifest: boolean; media: boolean; files: number | null }[]; newest_age_s: number | null }>("api/backups"),
  backupCreate: (label: string, media: boolean) => post<{ name: string; verify: { ok: boolean; problems: string[] } }>("api/backups", { label, media }),
  backupVerify: (name: string) => post<{ ok: boolean; problems: string[]; files: number; tables: number }>(`api/backups/${encodeURIComponent(name)}/verify`),
  savePreview: (cid: number, saveId: number) => req<{ save_point: { id: number; label: string; created_at: string }; changes: { what: string; now: string; after_restore: string }[];
    items: string[]; items_total: number; quests: string[]; characters: string[]; messages_hidden: number; note: string }>(`api/campaigns/${cid}/saves/${saveId}/preview`),
  problemReport: (cid: number, turnId: number | null, note: string) => post<{ path: string; markdown: string; turn_id: number | null }>(`api/campaigns/${cid}/reports`, { turn_id: turnId, note }),
  places: (id: number) => req<KnownWorld>(`api/campaigns/${id}/places`),
  journal: (id: number) => req<{ kinds: { id: string; label: string }[]; quests: JournalQuest[] }>(`api/campaigns/${id}/journal`),
  loreNotebook: (id: number) => req<LoreFact[]>(`api/campaigns/${id}/lore-notebook`),
  chronology: (id: number) => req<Chronology>(`api/campaigns/${id}/chronology`),
  search: (id: number, q: string) => req<SearchHit[]>(`api/campaigns/${id}/search?q=${encodeURIComponent(q)}`),
  messages: (id: number, before?: number) => req<Message[]>(`api/campaigns/${id}/messages${before ? `?before=${before}` : ""}`),
  houseRules: (id: number) => req<HouseRule[]>(`api/campaigns/${id}/house-rules`),
  rulings: (id: number) => req<Ruling[]>(`api/campaigns/${id}/rulings`),
  reviewRuling: (id: number, rid: number, decision: "approve" | "reject" | "reopen", note?: string) => post<Ruling>(`api/campaigns/${id}/rulings/${rid}/review`, { decision, note }),
  lore: (id: number) => req<{ id: number; subject: string; fact: string; certainty: string; source: string }[]>(`api/campaigns/${id}/lore`),
  records: (id: number, q?: string) => req<{ id: number; document_path: string; section: string; snippet?: string }[]>(`api/campaigns/${id}/records${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  record: (id: number, sid: number) => req<{ id: number; document_path: string; section: string; content: string }>(`api/campaigns/${id}/records/${sid}`),
  submitTurn: (id: number, text: string, key: string) => post<TurnStatus>(`api/campaigns/${id}/turns`, { text, idempotency_key: key }),
  turn: (turnId: number) => req<TurnStatus>(`api/turns/${turnId}`),
  activeTurn: (id: number) => req<TurnStatus | null>(`api/campaigns/${id}/turns/active`),
  saves: (id: number) => req<SavePoint[]>(`api/campaigns/${id}/saves`),
  createSave: (id: number, label: string) => post<{ save_point_id: number }>(`api/campaigns/${id}/saves`, { label }),
  restore: (id: number, sid: number) => post<{ restored: number; backup_save_point_id: number }>(`api/campaigns/${id}/saves/${sid}/restore`),
  ruleSearch: (q: string, cid?: number) => req<{ campaign: (HouseRule & { score: number })[]; official: Passage[] }>(`api/rules/search?q=${encodeURIComponent(q)}${cid ? `&cid=${cid}` : ""}`),
  rulePage: (doc: number, page: number) => req<{ document: string; page: number; page_count: number; text: string }>(`api/rules/page?document_id=${doc}&page=${page}`),
  ruleDocs: () => req<{ documents: { id: number; title: string; filename: string; page_count: number; chunks: number }[]; index_status: Record<string, any> }>("api/rules/documents"),
  indexRules: (directory?: string) => post<Record<string, any>>("api/rules/index", { directory }),
  ruleLookup: (q: string, cid?: number) => req<RuleLookup>(`api/rules/lookup?q=${encodeURIComponent(q)}${cid ? `&cid=${cid}` : ""}`),
  ruleSuggest: (q: string, cid?: number) => req<RuleSuggestion[]>(`api/rules/suggest?q=${encodeURIComponent(q)}${cid ? `&cid=${cid}` : ""}`),
  ruleBrowse: (bookId?: number) => req<RuleBrowse>(`api/rules/browse${bookId ? `?book_id=${bookId}` : ""}`),
  ruleCite: (ref: string) => req<{ document_id: number; page: number; document: string }>(`api/rules/cite?ref=${encodeURIComponent(ref)}`),
  settings: () => req<Prefs>("api/settings"),
  saveSettings: (p: Partial<Prefs>) => req<Prefs>("api/settings", { method: "PUT", body: JSON.stringify(p) }),
  importLedger: (path?: string) => post<{ campaign_id: number | null; report: any }>("api/import", { path }),
  importReports: () => req<{ id: number; campaign_id: number | null; source_path: string; created_at: string; report: any }[]>("api/import/reports"),
  ledgerPreview: (id: number, fetch = true) => req<LedgerPreview>(`api/campaigns/${id}/ledger/preview?fetch=${fetch}`),
  ledgerApply: (id: number, body: { fingerprint: string; selected: string[]; message: string; author_name: string; author_email: string; regenerate_checkpoint: boolean }) =>
    post<{ commit: string; summary: { changes: number; files: string[]; skipped: number; conflicts: number }; checkpoint: string | null }>(`api/campaigns/${id}/ledger/apply`, body),
  ledgerPull: (id: number) => post<LedgerPreview["status"]>(`api/campaigns/${id}/ledger/pull`),
  ledgerPush: (id: number) => post<LedgerPreview["status"]>(`api/campaigns/${id}/ledger/push`),
  ledgerHistory: (id: number) => req<{ id: number; base_commit: string; new_commit: string; summary: { changes: number; files: string[] }; created_at: string }[]>(`api/campaigns/${id}/ledger/history`),
  access: () => req<{ passcode: string; lan_addresses: string[]; path: string; port: number; fixed_by_env: boolean }>("api/access"),
  regeneratePasscode: () => post<{ passcode: string }>("api/access/regenerate"),
  manifest: () => req<Manifest>("api/assets/manifest"),
  gmDiagnosticsList: (id: number, token: string) => req<DiagnosticsRow[]>(`api/gm/campaigns/${id}/diagnostics`, { headers: { "X-GM-Token": token } }),
  gmDiagnostics: (turnId: number, token: string) => req<Record<string, any>>(`api/gm/turns/${turnId}/diagnostics`, { headers: { "X-GM-Token": token } }),
  artStatus: () => req<Record<string, any>>("api/art/status"),
  portraitCrop: (cid: number, charId: number, crop: { cx: number; cy: number; k: number; ar: number } | null) =>
    req<{ portrait_crop: unknown }>(`api/campaigns/${cid}/characters/${charId}/portrait-crop`, { method: "PUT", body: JSON.stringify(crop ?? {}) }),
  geminiQuota: () => req<Record<string, any>>("api/gemini/quota"),
  importVisualReference: (cid: number, markdown: string, source: string) =>
    req<{ updated: string[]; unmatched: string[]; save_point_id: number }>(`api/campaigns/${cid}/visual-reference`, { method: "POST", body: JSON.stringify({ markdown, source }) }),
  artDraftAppearance: (cid: number, subjectId: number) =>
    req<{ appearance: string }>(`api/campaigns/${cid}/art/draft-appearance`, { method: "POST", body: JSON.stringify({ kind: "character", subject_id: subjectId }) }),
  consistency: (cid: number) => req<{ ok: boolean; failed: number; checks: { check: string; ok: boolean; values: Record<string, unknown>; note?: string }[] }>(`api/campaigns/${cid}/consistency`),
  artDraftItemLooks: (cid: number, body: { item_ids?: number[]; overwrite?: boolean } = {}) =>
    post<{ drafted: { id: number; name: string; appearance: string; avoid?: string; features?: string }[]; skipped: number; remaining: number }>(`api/campaigns/${cid}/art/draft-item-looks`, body),
  artAppearances: (cid: number, kind: ArtKind) => req<Record<string, string>>(`api/campaigns/${cid}/art/appearances?kind=${kind}`),
  artList: (cid: number, p: { kind?: ArtKind; subject_id?: number | null; subject_name?: string }) => {
    const q = new URLSearchParams(Object.entries(p).filter(([, v]) => v != null && v !== "").map(([k, v]) => [k, String(v)]));
    return req<ArtJob[]>(`api/campaigns/${cid}/art?${q}`);
  },
  artPreview: (cid: number, kind: ArtKind, subjectId?: number | null, subjectName?: string, engine?: string) => {
    const q = new URLSearchParams({ kind, ...(subjectId != null ? { subject_id: String(subjectId) } : {}), ...(subjectName ? { subject_name: subjectName } : {}), ...(engine ? { engine } : {}) });
    return req<{ subject_name: string; prompt: string; engine: string; negative: string; size: [number, number]; appearance: string; avoid?: string; features?: string; unestablished: string[] }>(`api/campaigns/${cid}/art/preview?${q}`);
  },
  artAppearance: (cid: number, body: { kind: ArtKind; subject_id?: number | null; subject_name?: string; appearance: string; avoid?: string; features?: string }) =>
    req<{ ok: boolean }>(`api/campaigns/${cid}/art/appearance`, { method: "PUT", body: JSON.stringify(body) }),
  artDraftPlaceLooks: (cid: number, body: { keys?: string[]; overwrite?: boolean } = {}) =>
    post<{ drafted: { key: string; name: string; appearance: string; avoid: string; features?: string }[]; skipped: number; remaining: number }>(`api/campaigns/${cid}/art/draft-place-looks`, body),
  artRefs: (cid: number, charId: number) => req<{ name: string; url: string }[]>(`api/campaigns/${cid}/characters/${charId}/art-refs`),
  artRefUpload: (cid: number, charId: number, file: File) => {
    const fd = new FormData(); fd.append("file", file);
    return req<{ name: string; url: string }[]>(`api/campaigns/${cid}/characters/${charId}/art-refs`, { method: "POST", body: fd });
  },
  artRefDelete: (cid: number, charId: number, name: string) =>
    req<{ name: string; url: string }[]>(`api/campaigns/${cid}/characters/${charId}/art-refs/${encodeURIComponent(name)}`, { method: "DELETE" }),
  artCreate: (cid: number, body: { kind: ArtKind; subject_id?: number; subject_name?: string; prompt?: string; engine?: string }) =>
    req<ArtJob>(`api/campaigns/${cid}/art`, { method: "POST", body: JSON.stringify(body) }),
  artAction: (id: number, action: "approve" | "dismiss" | "retry" | "use") => req<ArtJob>(`api/art/${id}/${action}`, { method: "POST" }),
  gmRuntime: (token: string) => req<Record<string, any>>("api/gm/runtime", { headers: { "X-GM-Token": token } }),
  gmPromptCapture: (minutes: number, token: string) =>
    req<{ active: boolean; minutes: number }>("api/gm/runtime/prompt-capture", { method: "POST", headers: { "X-GM-Token": token, "Content-Type": "application/json" }, body: JSON.stringify({ minutes }) }),
  makeDemo: () => post<{ campaign_id: number }>("api/campaigns/demo"),
  uploadPortrait: (id: number, cid: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{ portrait_path: string }>(`api/campaigns/${id}/characters/${cid}/portrait`, { method: "POST", body: fd });
  },
};

export const fmtMinutes = (m: number | null | undefined) => (m == null ? "?" : `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`);
export const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
