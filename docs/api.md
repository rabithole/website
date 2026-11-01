# API reference

All endpoints are served by `server.py` on the same origin as the site.
Request and response bodies are JSON unless noted. Timestamps are **Unix
milliseconds**.

**Auth:** 🔒 = requires `Authorization: Bearer <token>` (from `POST /api/login`).
Missing/expired token → `401 {"error": "Unauthorized"}`.
👁 = public, but a signed-in owner also sees Draft items.

Errors are always `{"error": "<message>"}` with a 4xx/5xx status.

---

## Auth & account

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| POST | `/api/login` | — | `{username, password}` | `{ok, token, user, expiresAt}`. 401 bad credentials, 429 after 8 failures / 15 min per IP. |
| POST | `/api/logout` | token optional | — | `{ok: true}`; deletes the session. |
| GET | `/api/me` | 🔒 | — | `{user: {id, username, displayName}}` |
| PUT | `/api/me` | 🔒 | `{currentPassword, newUsername?, newPassword?, newDisplayName?}` | `{ok, user}`. 401 wrong current password, 409 username taken. Signs out your other sessions. |

## Products

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/products` | 👁 | Public sees only `published = Published`. Newest first. |
| GET | `/api/products/<id>` | 👁 | 404 for drafts unless signed in. |
| POST | `/api/products` | 🔒 | Create. Always saved as Draft. Returns 201 + product. |
| PUT | `/api/products/<id>` | 🔒 | Full update (send all fields). `published` is only changed if you send it. |
| DELETE | `/api/products/<id>` | 🔒 | `{ok: true}` |
| POST | `/api/products/<id>/backorder-request` | — | Public backorder request. Only allowed when status is Sold Out. Returns updated product. |

**Product fields** (request → response):

| Field | Type | Notes |
|---|---|---|
| `name` | string | required |
| `price` | number | required |
| `category` | string | required |
| `description` | string | required |
| `status` | string | default `Available`; `Sold Out` blocks checkout |
| `published` | `Draft` \| `Published` | response; settable on PUT |
| `image` | string | path, e.g. `assets/uploads/upload-….png` |
| `imageDescription` | string | optional, ≤ 500 chars |
| `shippingInfo` | string | optional |
| `stockQuantity` | int \| null | null = unlimited |
| `displayStatus` | string | response only — `Backordered` when Sold Out with ≥ 3 requests |
| `backorderRequests` | int | response only |
| `id`, `createdAt`, `updatedAt` | | response; `id` like `prod-<ms>` |

## Blog posts & categories

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/posts` | 👁 | Public sees `status = Published` only. |
| GET | `/api/posts/<id>` | 👁 | |
| POST | `/api/posts` | 🔒 | `{title, content, tags[], image?, imageDescription?, category?, date?}`. Saved as Draft. |
| PUT | `/api/posts/<id>` | 🔒 | Same fields + `status` (only changed if sent). |
| DELETE | `/api/posts/<id>` | 🔒 | |
| GET | `/api/blog-categories` | — | `[{id, name, createdAt}]`, sorted by name. |
| POST | `/api/blog-categories` | 🔒 | `{name}`. 409 if it exists (case-insensitive). |
| DELETE | `/api/blog-categories/<id>` | 🔒 | |

## Projects

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/projects` | 👁 | |
| GET | `/api/projects/<id>` | 👁 | |
| POST | `/api/projects` | 🔒 | `{title, description, content?, image, imageDescription?, images[], tags[]}`. Saved as Draft. |
| PUT | `/api/projects/<id>` | 🔒 | Same + `status`. |
| DELETE | `/api/projects/<id>` | 🔒 | |

`images` is a gallery: `[{ "src": "...", "description": "optional caption ≤ 500 chars" }]`.

## Image uploads

Body is the **raw image bytes**, not JSON. `Content-Type` must be
`image/png`, `image/jpeg`, `image/webp` or `image/gif`. Max 8 MB.

| Method | Path | Auth | Returns |
|---|---|---|---|
| POST | `/api/upload` | 🔒 | `201 {path: "assets/uploads/upload-<ms>-<hex>.<ext>"}` |
| POST | `/api/requests/upload` | — | `201 {path: "assets/uploads/request-<ms>-<hex>.<ext>"}` |

## Checkout (PayPal)

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| GET | `/api/paypal/config` | — | — | `{clientId, currency, mode, configured}` (never the secret) |
| POST | `/api/paypal/orders` | — | `{items: [{productId, quantity}]}` | `201 {id}` (PayPal order id). 503 if sales paused or PayPal not configured; 400 sold out / over stock / bad qty; 502 PayPal error. |
| POST | `/api/paypal/orders/<id>/capture` | — | — | `{status, orderIds}`; records orders and updates stock. |

## Orders (owner)

Orders are stored one row per line item but returned **grouped by PayPal order**.

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| GET | `/api/orders` | 🔒 | — | `[{paypalOrderId, createdAt, currency, payerEmail, payerName, status, fulfillmentStatus, trackingNumber, shipping{…}, totalAmount, items[]}]` |
| PUT | `/api/orders/group/<paypalOrderId>` | 🔒 | `{fulfillmentStatus?, trackingNumber?}` | Updated group. Status ∈ `Pending`, `Shipped`, `Delivered`, `Cancelled`. |
| DELETE | `/api/orders/group/<paypalOrderId>` | 🔒 | — | Deletes every line item in that order. |

## Custom work requests

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/api/requests` | — | `{name, email, serviceType, description, budget?, timeline?, photo?}` → 201. First four required. Status starts `New`. |
| GET | `/api/requests` | 🔒 | Newest first. |
| PUT | `/api/requests/<id>` | 🔒 | `{status}` ∈ `New`, `In Progress`, `Completed`, `Declined`. |
| DELETE | `/api/requests/<id>` | 🔒 | |

## Store settings

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| GET | `/api/store-status` | — | — | `{salesPaused}` |
| PUT | `/api/store-status` | 🔒 | `{salesPaused: bool}` | Pauses/resumes all checkout. |
| GET | `/api/quick-stats` | — | — | `{stats: [4 × {value, label}], enabled}` (live values already filled in) |
| GET | `/api/quick-stats/settings` | 🔒 | — | `{mode, custom, enabled, liveSlots, defaults}` |
| PUT | `/api/quick-stats/settings` | 🔒 | `{mode: "live"\|"custom", custom: [4 × {value, label}], enabled}` | `{mode, enabled, stats}` |

## Adding a new endpoint

1. Add a branch in the matching `do_GET` / `do_POST` / `do_PUT` / `do_DELETE`
   in `server.py`. More specific paths must come **before** broader
   `startswith` checks (e.g. `/backorder-request` before `/api/products`).
2. Call `self._require_auth()` first for owner-only routes.
3. Return with `self._send_json(data, status)`.
4. Add a wrapper function in `assets/api.js` and export it on `RabitholeDB`.
5. If you add a new HTML page, add it to `ALLOWED_STATIC_FILES` or it will 404.
6. Update the docstring at the top of `server.py` and this file.
