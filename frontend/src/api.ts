// Typed client for the player API. The frontend never calls /api/gm: GM-only data is not part of this bundle.

export type Stats = { stats?: Record<string, number>; modifiers?: Record<string, number>; unenhanced?: Record<string, number> };
export type Status = { id: number; name: string; description?: string | null; polarity: string; expires_at_minutes?: number | null };
export type CharacterCard = {
  id: number; slug: string; name: string; kind: string; roster_category?: string | null; crawler_number?: string | null; species?: string | null; race?: string | null;
  class_name?: string | null; level?: number | null; status: string; location?: string | null; in_party: number; party_order: number;
  hp_current?: number | null; hp_max?: number | null; mana_current?: number | null; mana_max?: number | null; ai_favor?: number | null;
  stats: Stats; derived: Record<string, unknown>; portrait_path?: string | null; statuses: Status[]; equipped: { id: number; name: string; slot?: string }[];
  personality?: string | null; known_motivation?: string | null; attitude?: number | null; voice?: string | null; assignment?: string | null;
};
export type Skill = { id: number; kind: string; name: string; rank: number | null; mana_cost: number | null; passive: number; marked: number; description?: string | null; properties: Record<string, unknown> };
export type Item = { id: number; owner_character_id: number | null; owner_name?: string | null; name: string; quantity: number | null; equipped: number; deployed?: number; slot?: string | null; rarity?: string | null; description?: string | null; properties: Record<string, unknown> };
export type CharacterSheet = CharacterCard & {
  bio?: string | null; sheet: Record<string, unknown>; skills: Skill[]; items: Item[];
  achievements: { title: string; description?: string; game_time_minutes?: number }[];
  npc_profile: { personality?: string; voice?: string; known_motivation?: string; attitude?: number | null } | null;
  relationships: { kind: string; description?: string; a_name: string; b_name: string }[];
  limited_uses: { ability: string; per: string; uses: number; uses_allowed: number; cooldown_minutes: number | null }[];
};
export type Quest = { id: number; kind: string; title: string; description?: string | null; status: string; priority: number; progress?: string | null; giver?: string | null;
  details?: Record<string, unknown>; due_at_minutes?: number | null };
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
};
export type Combatant = { id: string; name: string; side: "party" | "foe"; defeated?: boolean; hb_percent?: number; actions_left?: number;
  evading?: boolean; hp_current?: number | null; hp_max?: number | null; dead?: boolean; dying?: string | null };
export type Encounter = { id: number; name: string; round: number; phase?: string; summary?: string | null; combatants: Combatant[] };
export type CampaignListItem = { id: number; name: string; is_demo: number; data_source: string; current_floor: number | null; current_location: string | null };
export type RollCard = { roll_id: number; expression: string; dice: { sides: number; value: number; kept: boolean }[]; total: number; target: number | null;
  outcome: string | null; degree: string | null; reason: string | null; detail: Record<string, any> };
export type Message = { id: number; turn_id: number | null; role: "player" | "gm" | "system"; content: string; created_at: string;
  meta: { rolls?: RollCard[]; changes?: string[]; rules_cited?: string[]; provider?: string; floor_clock?: FloorClock; style?: string;
    cards?: { type: string; summary: string }[] } };
export type Manifest = { locations: Record<string, string>; characters: Record<string, string> };
export type DiagnosticsRow = { turn_id: number; status: string; input: string; created_at: string; rounds: number; flags: number; input_tokens: number };
export type TurnStatus = { turn_id: number; status: "processing" | "completed" | "failed"; replayed: boolean; error: string | null;
  result: { narration: string; rolls: RollCard[]; changes: string[]; notices: { text: string; style: string }[] } | null;
  progress: { stage: string; detail: string; elapsed_s: number } | null };
export type EventRow = { id: number; type: string; summary: string; game_time_minutes: number | null; created_at: string };
export type HouseRule = { id: number; section: string | null; title: string; text: string; label: string | null; status: string; mechanical_status: string; source: string | null };
export type Passage = { chunk_id: number; document_id: number; document: string; page: number; ref: string; text: string; score: number };
export type SavePoint = { id: number; label: string; kind: string; turn_id: number | null; created_at: string };
export type Prefs = { gm_provider: string; codex_model: string; gemini_model: string; gemini_key_present?: boolean; codex_reasoning_effort: string; rulebook_dir: string; ledger_dir: string;
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
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch { /* not json */ }
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
  inventory: (id: number) => req<{ shared: Item[]; by_character: Item[] }>(`api/campaigns/${id}/inventory`),
  quests: (id: number) => req<Quest[]>(`api/campaigns/${id}/quests`),
  npcs: (id: number) => req<CharacterCard[]>(`api/campaigns/${id}/npcs`),
  timeline: (id: number) => req<EventRow[]>(`api/campaigns/${id}/timeline`),
  messages: (id: number, before?: number) => req<Message[]>(`api/campaigns/${id}/messages${before ? `?before=${before}` : ""}`),
  houseRules: (id: number) => req<HouseRule[]>(`api/campaigns/${id}/house-rules`),
  rulings: (id: number) => req<{ id: number; topic: string; ruling: string; certainty: string; label: string; created_at: string }[]>(`api/campaigns/${id}/rulings`),
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
