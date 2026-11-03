# Carousel

Single-player, AI-game-mastered horror roleplaying game set in Carousel, the town from Rob M. Lastrel's *The Game at
Carousel*, where horror movies happen for real. Forked from Crawler Console (`E:\dccrpg`); the two apps run side by
side and share nothing at runtime.

* Design: [docs/2026-10-30-carousel-game-design.md](docs/2026-10-30-carousel-game-design.md)
* Architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
* Roadmap and plans: [docs/superpowers/plans/](docs/superpowers/plans/)

## Running

| Script | What it does |
|---|---|
| `start.cmd` | Creates the Python environment, builds the interface and imports the data if needed, opens <http://127.0.0.1:8797>. |
| `serve.cmd` | Runs the server without a browser (does nothing if the port is already in use). |
| `import-data.cmd` | Rebuilds `data/reference.db` and the rule index from the Carousel source packages. Refuses, keeping the old data, if a saved campaign would lose an ID. |
| `build-frontend.cmd` | Type-checks and builds the React interface into `frontend/dist`. |
| `run-tests.cmd` | Runs the backend tests (pass a path to run one file). |

Carousel source data is read (never written) from `CAROUSEL_SOURCE_DIR` (default `E:\Carousel`). Game data lives in
`CAROUSEL_DATA_DIR` (default `data/`): `campaign.db` for play, and `reference.db` and `rulebooks.db`, which the importer
rebuilds whole and swaps in.

The player sees only what their character knows. Everything GM-only (sealed story truth, Omen leads, unreached rooms,
death causes) is served only under `/api/gm` with the GM token; `tests/test_visibility.py` checks every player endpoint.
