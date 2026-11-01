# Database

One SQLite file: `db/rabithole.db`. It is **gitignored** because it contains
password hashes and customer names, emails and shipping addresses. It is never
in GitHub, and the deploy never touches it.

- Live copy: on the VPS, inside the deployed `db/` folder.
- Local copy: `db/rabithole.db` on the dev PC (separate data from production).

## How the schema is managed

There is no migration tool. On every start, `main()` in `server.py`:

1. Exits with `ERROR: Database not found` if the file doesn't exist.
2. Runs `CREATE TABLE IF NOT EXISTS` for `users`, `sessions`, `orders`,
   `settings`, `requests`, `projects`, `blog_categories`.
3. Checks columns with `PRAGMA table_info` and runs `ALTER TABLE … ADD COLUMN`
   for anything missing.
4. Seeds two example projects if `projects` is empty.

To add a column: add an `if "<col>" not in <table>_cols: ALTER TABLE …` block
in `main()`, then read it with a `"col" in row.keys()` guard in the matching
`row_to_*` function so older databases still work.

> ⚠️ `products` and `posts` are **not** created by the server — only altered.
> A brand-new database needs them created first (see bottom of this page).

## Tables

### `users` — owner accounts
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | |
| username | TEXT UNIQUE | |
| password_hash | TEXT | PBKDF2-SHA256, 100k iterations, hex |
| salt | TEXT | random hex |
| display_name | TEXT | |
| created_at | INTEGER | ms |

### `sessions`
| Column | Type | Notes |
|---|---|---|
| token | TEXT PK | random, URL-safe |
| user_id | TEXT | → users.id |
| created_at, expires_at | INTEGER | ms; 7-day lifetime |

### `products`
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | `prod-<ms>` |
| name, category, description | TEXT | |
| price | REAL | |
| status | TEXT | `Available`, `Sold Out`, … |
| published | TEXT | `Draft` / `Published` (default Published for pre-existing rows) |
| image, image_description | TEXT | |
| shipping_info | TEXT | |
| stock_quantity | INTEGER | NULL = unlimited |
| backorder_requests | INTEGER | default 0 |
| created_at, updated_at | INTEGER | ms |

### `posts`
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | `post-<ms>` |
| title, content | TEXT | |
| tags | TEXT | JSON array |
| image, image_description | TEXT | |
| status | TEXT | `Draft` / `Published` |
| category | TEXT | category name |
| date | TEXT | `YYYY-MM-DD` |
| created_at, updated_at | INTEGER | ms |

### `blog_categories`
| id TEXT PK | name TEXT UNIQUE | created_at INTEGER |
|---|---|---|

### `projects`
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | `project-<ms>` |
| title, description, content | TEXT | |
| image, image_description | TEXT | cover image |
| images | TEXT | JSON array of `{src, description}` |
| tags | TEXT | JSON array |
| status | TEXT | `Draft` / `Published` |
| created_at, updated_at | INTEGER | ms |

### `orders` — one row per line item
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | `order-<ms>-<index>` |
| paypal_order_id | TEXT | groups line items into one checkout |
| product_id, product_name | TEXT | product_id NULL for fallback "Cart order" rows |
| amount | REAL | unit price × quantity |
| currency | TEXT | |
| payer_email, payer_name | TEXT | |
| status | TEXT | PayPal capture status, e.g. `COMPLETED` |
| fulfillment_status | TEXT | `Pending` (default), `Shipped`, `Delivered`, `Cancelled` |
| tracking_number | TEXT | |
| shipping_name, shipping_address_line1, shipping_address_line2, shipping_city, shipping_state, shipping_postal_code, shipping_country | TEXT | from PayPal |
| product_shipping_info | TEXT | copy of the product's shipping note at purchase time |
| created_at | INTEGER | ms |

### `requests` — custom work requests
| Column | Type | Notes |
|---|---|---|
| id | TEXT PK | `request-<ms>` |
| name, email, service_type, description | TEXT | required |
| budget, timeline, photo | TEXT | optional |
| status | TEXT | `New`, `In Progress`, `Completed`, `Declined` |
| created_at | INTEGER | ms |

### `settings` — key/value
| Key | Values |
|---|---|
| `sales_paused` | `"0"` / `"1"` |
| `quick_stats_enabled` | `"0"` / `"1"` |
| `quick_stats_mode` | `custom` / `live` |
| `quick_stats_custom` | JSON list of 4 `{value, label}` |

## Backups

The database is the only copy of orders, customers, products, posts and
projects. Back it up from the server regularly, e.g.:

```bash
# on the VPS, from the app folder
sqlite3 db/rabithole.db ".backup 'db/backup-$(date +%F).db'"
```

Then copy the backup off the server. `.backup` is safe while the server is running;
a plain file copy mid-write might not be. Also back up `assets/uploads/`.

## Creating a database from scratch

Only needed for a fresh machine with no existing `rabithole.db`:

```bash
mkdir -p db
sqlite3 db/rabithole.db <<'SQL'
CREATE TABLE products (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL,
  category TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Available',
  description TEXT NOT NULL, image TEXT,
  created_at INTEGER NOT NULL, updated_at INTEGER
);
CREATE TABLE posts (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, tags TEXT, content TEXT NOT NULL,
  date TEXT, created_at INTEGER NOT NULL, updated_at INTEGER
);
SQL
python server.py          # adds every other table and column
python manage_users.py add --username <you> --display-name "<Your Name>"
```

The `CREATE TABLE` statements above are the minimum the server needs; the
remaining columns are added automatically on first start.
