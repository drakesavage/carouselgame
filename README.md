# Carousel

Single-player, AI-game-mastered horror roleplaying game set in the town of Carousel. Forked from Crawler Console
(`E:\dccrpg`, remote `dccrpg`); the two apps run side by side and share nothing at runtime.

* Design: [docs/2026-10-30-carousel-game-design.md](docs/2026-10-30-carousel-game-design.md)
* Roadmap and plans: [docs/superpowers/plans/](docs/superpowers/plans/)

## Running

`start.cmd` creates the Python environment, builds the interface if needed and opens <http://127.0.0.1:8797>.
`serve.cmd` runs the server without a browser. `run-tests.cmd` runs the backend tests.

Carousel source data is read (never written) from `CAROUSEL_SOURCE_DIR` (default `E:\Carousel`).
Game data lives in `CAROUSEL_DATA_DIR` (default `data/`).
