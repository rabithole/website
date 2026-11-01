# Rabithole

Storefront, portfolio and blog for 3D-printed RC parts and engineering projects,
designed and sold by a mechanical engineering student. Live at
**https://www.rabithole.com**.

Plain HTML/CSS/JS frontend and a small Python server (standard library only)
with SQLite storage, PayPal checkout and an owner dashboard. Pushing to `master`
deploys to production automatically.

## Documentation

| Doc | Covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | How the pieces fit together: pages, auth, checkout flow, known limitations |
| [docs/api.md](docs/api.md) | Every `/api` endpoint, its fields and how to add a new one |
| [docs/database.md](docs/database.md) | SQLite schema, migrations, backups, creating a fresh DB |
| [docs/deployment.md](docs/deployment.md) | GitHub Actions → VPS deploy, secrets, rollback, troubleshooting |
| [docs/operations.md](docs/operations.md) | Running the store: products, orders, requests, Quick Stats, PayPal, accounts |

## Quick start (local)

Requires **Python 3.10+**. No packages to install.

```bash
python server.py
```

Open http://localhost:8080. Sign in at `/login.html`.

The server needs an existing `db/rabithole.db` and exits with
`ERROR: Database not found` without one. The database is gitignored, so a fresh
clone doesn't have it. Copy one in, or create one: see
[database.md → Creating a database from scratch](docs/database.md#creating-a-database-from-scratch).

Create an owner login:

```bash
python manage_users.py add --username <you> --display-name "<Your Name>"
```

PayPal checkout stays disabled until `paypal_config.json` has keys. Use
**sandbox** keys locally (see [operations.md → PayPal](docs/operations.md#paypal)).

## Project structure

```
index.html              Homepage (hero, about, sales, education, blog cards, Quick Stats)
products.html           Shop + cart + PayPal checkout
projects.html           Project portfolio with photo galleries
blog.html               Blog listing with categories
request.html            Custom work request form
contact.html            Contact page
login.html              Owner sign-in

admin.html              Owner dashboard (pause sales, Quick Stats editor)
manage-products.html    ┐
add-product.html        ┘ Products
manage-projects.html    ┐
add-project.html        ┘ Projects
manage-posts.html       ┐
create-post.html        ┘ Blog posts + categories
orders.html             Order history, fulfillment, tracking
manage-requests.html    Custom work requests
account.html            Change username / display name / password

server.py               HTTP server, JSON API, SQLite schema/migrations, PayPal calls
manage_users.py         CLI for owner accounts
assets/api.js           Frontend data layer (window.RabitholeDB)
assets/cart.js          Cart drawer + PayPal buttons
assets/*.png|jpg        Site images
.github/workflows/      Deploy to production on push to master
docs/                   Project documentation

# gitignored — never commit
db/                     SQLite database (users, orders, customer data)
paypal_config.json      PayPal Client ID / Secret
assets/uploads/         Uploaded images
Working/                Personal notes
```

## Branches

- **`master`**: the site. Every push deploys.
- `main`: leftover "Initial commit" (LICENSE only). Not used.
