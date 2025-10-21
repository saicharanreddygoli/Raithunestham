# RaithuNestham — Agent Notes

## Application Overview
- Single-file Flask application (`app.py`) serving a multi-role platform for farmers, workers, volunteers, and an admin view.
- MongoDB backs all persistent data with collections for users (`farmers`, `workers`, `volunteers`), work `tasks`, assistance `requests`, and cross-role `notifications`.
- Geolocation features rely on `geopy` (Nominatim) with retry/backoff and fallback coordinates for Gudlavalleru mandal villages. Tasks and workers use MongoDB 2dsphere indexes to support distance-based filtering (Haversine helper in Python).
- UI delivered through Jinja templates in `templates/`, styled primarily with Tailwind CDN plus Bootstrap for the admin panel. Client-side translations handled by `static/js/i18n.js`.

## Key Runtime Details
- Environment variables: `FLASK_SECRET_KEY` (required for consistent sessions), `MONGODB_URI`, `MONGODB_DB`, `FLASK_DEBUG`, `FLASK_RUN_PORT`, and optional `ENABLE_DUPLICATE_TASK_CLEANUP`.
- Session keys: `farmer_id`, `worker_id`, `volunteer_id`, plus `preferred_lang` for English/Telugu localization.
- Collections create indexes at startup (`coordinates` 2dsphere, unique open-task constraint) and optionally clean up duplicates when the cleanup flag is enabled.
- Notifications are inserted for farmers when workers accept a task, and for workers when volunteers accept support requests.

## Notable Behaviors & Flows
- Farmers post jobs (task type, wage, workers, notes) and now manage them end‑to‑end: confirm arrivals, mark offline payments, rate workers (1–5), delete posts (with notifications cleaned up), and trigger volunteering help. A navbar bell surfaces live notifications with a one-click clear.
- Workers register with skills, preferred wage, and availability; the dashboard highlights their profile, shows nearby tasks within **30 km**, offers map navigation, tracks assignment status chips (confirmed/arrived/paid), and lists recent completions with ratings.
- Volunteers respond to farmer and worker help requests, seeing worker wage/skill/rating context, and can post tasks on behalf of farmers. Worker auto-help requests route to up to three nearby volunteers automatically.
- Admin view aggregates totals plus worker rating averages/counts, recent activity, and village trends.
- Village data loads from `villages.json` if present but falls back to the curated Gudlavalleru list defined in code. Notifications still insert for farmers when workers accept and for workers when volunteers accept support.

## Repository Hygiene (Current Session)
- Removed obsolete top-level HTML duplicates (`farmer*.html`, `volunteer*.html`, `index.html`) in favor of the maintained Jinja templates under `templates/`.
- Deleted transient runtime artifacts (`__pycache__/`, `cache/`) to keep the workspace clean—these should regenerate as needed.

## Open Questions / Possible Follow-ups
- Consider reconciling `villages.json` contents with the in-app Gudlavalleru list or documenting its intended structure if broader district support returns.
- Evaluate need for rate-limiting/geocoding error handling when scaling volunteer task creation (Nominatim usage policy).
- No automated tests are present; adding lightweight integration or route smoke tests could help guard core flows.
