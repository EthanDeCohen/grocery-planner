# Contributing to Grocery Planner

This project uses a simple **branch → pull request → review → merge** workflow. Direct pushes to `main` are blocked.

## Branch rules

| Branch | Purpose | Who can push |
|--------|---------|--------------|
| `main` | Stable, working code | **Nobody directly** — merge via PR only |
| `feat/*` | New features | You (via PR) |
| `fix/*` | Bug fixes | You (via PR) |
| `docs/*` | README / docs only | You (via PR) |
| `chore/*` | Tooling, deps, git config | You (via PR) |

## Standard workflow

### 1. Start from latest `main`

```powershell
cd C:\Users\edeco\OneDrive\Desktop\groceryPlanner
git checkout main
git pull origin main
```

### 2. Create a feature branch

Use a short, descriptive name:

```powershell
git checkout -b feat/foodlion-subcategory
# or: fix/scraper-timeout, docs/readme-scraping, chore/requirements
```

### 3. Make changes and commit

Keep commits focused — one logical change per commit when possible.

```powershell
git add <files>
git commit -m "Add sub_category for Food Lion no-price flyer items"
```

Good commit message format:

```
<type>: <short summary>

Optional longer explanation of why, not just what.
```

Types: `feat`, `fix`, `docs`, `chore`, `refactor`

### 4. Push the branch

```powershell
git push -u origin feat/foodlion-subcategory
```

### 5. Open a pull request

- Go to https://github.com/EthanDeCohen/grocery-planner/pulls
- Click **New pull request**
- Base: `main` ← Compare: your branch
- Fill in the PR template (summary, test plan, checklist)
- Review the **Files changed** tab before merging

### 6. Merge

- Use **Squash and merge** for small features (keeps `main` history clean)
- Use **Merge commit** if you want to preserve every commit on the branch
- Delete the branch after merge

### 7. Update local `main`

```powershell
git checkout main
git pull origin main
```

## What to include in every PR

1. **Summary** — what changed and why
2. **Test plan** — commands you ran and what you verified
3. **Screenshots / sample output** — for scraper or Excel changes, paste row counts or sample CSV lines
4. **Breaking changes** — new CSV columns, renamed scripts, etc.

## Scraper changes

If you change CSV column layouts (`prices.csv` or `deals.csv`):

1. Update `README.md` schema section
2. Run the scraper locally and confirm `RefreshGroceryData` still works in Excel
3. Note that `data/` is gitignored — describe output in the PR, don't commit personal CSVs

## Branch protection on `main`

`main` is protected with:

- Pull request required before merge
- Force pushes disabled
- Branch deletion disabled

## Quick command reference

```powershell
git status
git checkout -b feat/my-change
git push -u origin feat/my-change
gh pr create --base main --head feat/my-change   # if gh CLI is authenticated
```