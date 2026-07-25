# Rabithole

3D-printed RC parts storefront, blog, and owner dashboard — designed and sold by a mechanical engineering student. Static HTML/CSS/JS frontend backed by a small Python stdlib server with SQLite persistence and PayPal checkout.

## Requirements

- Python 3.10+ (no third-party packages — uses only the standard library)

## Running locally

```bash
python server.py
```

Then open `http://localhost:8080`.

The server requires `db/rabithole.db` to already exist (it does **not** create the database or seed a default user from scratch — see [Database](#database) below).

## Database

`db/rabithole.db` is a SQLite file and is **gitignored** — it holds password hashes and real order/customer data, so it never gets committed. This means:

- A fresh clone of this repo has no database and `server.py` will exit with `ERROR: Database not found`.
- You need your own copy of `db/rabithole.db` (with `users`, `sessions`, `orders`, `products`, and `posts` tables) placed in `db/` before the server will start.
- Keep a backup of `db/rabithole.db` somewhere outside git if you want to preserve products, posts, and order history.

Once a database file is present, `server.py` will create the `users`, `sessions`, and `orders` tables automatically if they're missing, but `products` and `posts` must already exist in the file.

## Managing owner accounts

```bash
python manage_users.py list
python manage_users.py add --username bob --display-name "Bob"
python manage_users.py update --username admin --new-username owner
python manage_users.py delete --username bob
```

If `--password`/`--new-password` is omitted you'll be prompted interactively so it never lands in shell history.

## PayPal checkout

Edit `paypal_config.json` (gitignored — never commit real keys) with your Client ID and Secret from the [PayPal Developer Dashboard](https://developer.paypal.com/dashboard/applications):

```json
{
  "mode": "sandbox",
  "client_id": "",
  "secret": "",
  "currency": "USD"
}
```

Use a **Sandbox** app to test with fake money first. Switch `"mode"` to `"live"` with live keys only when you're ready to accept real payments.

## Project structure

```
index.html           Landing page
products.html        Shop / product listing + PayPal checkout
blog.html            Blog listing
login.html           Owner sign-in
admin.html           Owner dashboard
add-product.html     Create/edit product (owner only)
create-post.html     Create/edit blog post (owner only)
orders.html          PayPal order history (owner only)
account.html         Owner account settings
server.py            HTTP + JSON API server, SQLite access, PayPal API calls
manage_users.py      CLI for managing owner accounts
assets/              Images and assets/api.js (frontend data layer)
db/                  SQLite database (gitignored)
paypal_config.json   PayPal credentials (gitignored)
```

## API

See the docstring at the top of `server.py` for the full endpoint list (auth, products, posts, PayPal orders, order history).
