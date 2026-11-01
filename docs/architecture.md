# Architecture

Rabithole is a single-process Python web app: one `server.py` serves the static
HTML/CSS/JS pages **and** a JSON API backed by one SQLite file. There is no
framework, no build step, and no third-party Python packages.

```
Browser ──HTTPS──▶ nginx (VPS) ──HTTP 127.0.0.1:8080──▶ server.py ──▶ db/rabithole.db
                                                          │
                                                          └──HTTPS──▶ PayPal REST API
```

## Components

| Piece | File(s) | Role |
|---|---|---|
| HTTP + API server | `server.py` | `ThreadingHTTPServer` on `127.0.0.1:8080`. Routes `/api/*` to handlers, serves an allow-list of static files, talks to PayPal. |
| Data layer (browser) | `assets/api.js` | Exposes `window.RabitholeDB` — every page calls the API through it. Attaches the auth token. Falls back to `localStorage` if the API is unreachable (e.g. opening the HTML files directly). |
| Cart + checkout (browser) | `assets/cart.js` | Cart drawer UI, cart state in `localStorage` (`rabithole_cart`), loads the PayPal JS SDK and renders PayPal buttons. Shows a banner when sales are paused. |
| Database | `db/rabithole.db` | SQLite. Gitignored. See [database.md](database.md). |
| Owner CLI | `manage_users.py` | Add / list / update / delete owner login accounts. |
| PayPal credentials | `paypal_config.json` | Gitignored. Created with blank values on first run if missing. |
| Uploaded images | `assets/uploads/` | Gitignored. Product, project, post and request photos land here. |
| Deploy | `.github/workflows/deploy.yml` | Push to `master` → SSH to the VPS → server's `deploy.sh`. See [deployment.md](deployment.md). |

## Pages

### Public

| Page | Purpose |
|---|---|
| `index.html` | Homepage: hero, about, products/sales section, education, latest blog cards (from the DB), Quick Stats row, contact links. |
| `products.html` | Shop. Lists published products; add to cart; PayPal checkout. |
| `projects.html` | RC/engineering project portfolio with photo galleries. |
| `blog.html` | Blog listing with category filter. |
| `request.html` | Custom-work request form (with optional reference photo). |
| `contact.html` | Static contact page. |
| `login.html` | Owner sign-in. |

### Owner-only (redirect to `login.html` if not signed in)

| Page | Purpose |
|---|---|
| `admin.html` | Dashboard: links to everything below, pause/resume sales, Quick Stats editor. |
| `manage-products.html` / `add-product.html` | List and create/edit products (stock, status, publish state, image). |
| `manage-projects.html` / `add-project.html` | List and create/edit projects (gallery images + captions). |
| `manage-posts.html` / `create-post.html` | List and create/edit blog posts; manage categories. |
| `orders.html` | PayPal order history; set fulfillment status and tracking number. |
| `manage-requests.html` | Review custom-work requests and set their status. |
| `account.html` | Change own username, display name, password. |

> The page-level redirect is only a convenience. **Real access control is on the
> server**: every owner-only API route checks the bearer token.

## Request flow

1. Browser requests a page → `server.py` checks the path against
   `ALLOWED_STATIC_FILES` / `ALLOWED_STATIC_PREFIXES` (`/assets/`). Anything
   else (e.g. `server.py`, `db/`, `paypal_config.json`, `.claude/`) returns 404.
2. The page loads `assets/api.js`, which calls `/api/...` on the same origin.
3. The API handler opens a SQLite connection per request, runs the query, closes it.
4. Responses are JSON with `Cache-Control: no-store` and **no CORS headers** —
   the frontend and API are always same-origin, so other sites cannot call the API.

## Authentication

- Owner accounts live in `users` (PBKDF2-SHA256, 100,000 iterations, per-user salt).
- `POST /api/login` returns a random session token (valid 7 days) stored in the
  `sessions` table. The browser keeps it in `sessionStorage` (`rabithole_token`)
  so it disappears when the tab is closed.
- Owner requests send `Authorization: Bearer <token>`.
- Failed logins are throttled per client IP: 8 failures within 15 minutes →
  HTTP 429. The real IP comes from nginx's `X-Forwarded-For`.
- Changing your password via `PUT /api/me` logs out all your *other* sessions.

## Checkout flow (PayPal)

1. `cart.js` sends the cart (`[{productId, quantity}]`) to `POST /api/paypal/orders`.
2. The server **re-prices everything from the database** (client prices are never
   trusted), checks sold-out / stock limits and the sales-paused flag, and
   creates a PayPal order (`intent: CAPTURE`, shipping address from the buyer's
   PayPal account).
3. The cart snapshot is held in memory (`PENDING_CARTS`) keyed by PayPal order id.
4. Buyer approves in the PayPal popup → `POST /api/paypal/orders/<id>/capture`.
5. On capture the server writes **one `orders` row per line item** (all sharing
   the PayPal order id), decrements `stock_quantity`, and flips a product to
   `Sold Out` when stock reaches 0.
6. If the server restarted between steps 2 and 4, the cart snapshot is lost; the
   capture still succeeds and records a single "Cart order" row using PayPal's
   total, so no payment goes unrecorded.

## Content lifecycle

- New products, posts and projects are always created as **Draft** and must be
  published explicitly with an edit.
- Products have two separate fields: `status` (Available / Sold Out / …) and
  `published` (Draft / Published).
- **Backordered**: when a Sold Out product gets 3+ shopper requests
  (`backorder_requests`), it *displays* as Backordered. The stored status stays
  Sold Out. Restocking (changing status away from Sold Out) resets the counter.

## Quick Stats (homepage)

Four tiles. Settings are stored in the `settings` table:

- `quick_stats_enabled` — show/hide the row.
- `quick_stats_mode` — `custom` (all four tiles use your text) or `live`
  (tile 0 = count of COMPLETED order rows, tile 2 = number of projects; tiles 1
  and 3 always use your text).
- `quick_stats_custom` — JSON list of the four `{value, label}` entries.
  `DEFAULT_QUICK_STATS` in `server.py` is the "reset to original" value.

## Known limitations

These are things to be aware of, not necessarily bugs:

- **Single process, in-memory state.** `PENDING_CARTS` and the login throttle
  reset on every restart (every deploy restarts the server).
- **Expired sessions are never purged** from the `sessions` table; they're just
  ignored. Harmless, but the table grows.
- **`POST /api/requests` and `/api/requests/upload` are public with no rate
  limit**, so they could be spammed (8 MB per upload).
- **Uploaded files are never deleted** when the product/post/project/request
  that used them is deleted.
- **"Parts Sold" counts order line-item rows, not quantities.** A line with
  quantity 3 counts as 1.
- `products` and `posts` tables are not created by `server.py` — a brand-new
  database needs them created by hand (see [database.md](database.md)).
- Browsers cache nothing (`no-store` on every response) — fine for this traffic
  level, but images re-download on every visit.
