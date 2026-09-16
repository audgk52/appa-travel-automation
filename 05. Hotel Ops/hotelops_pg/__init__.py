"""Hotel Ops · PG (Rooming List Update) — v1.

Implements the approved PRD v3.2 architecture (ARCHITECTURE CHECKPOINT PASSED).
The PRD is the behavior contract; this package does not invent material product
behavior. See ``05. Hotel Ops/PRD_HotelOps_PG_RoomingList.md``.

Only ``sheet_store.build_sheets_service`` touches Google (lazy import), mirroring
Dispatch, so the domain core imports and runs without the google packages.
"""
