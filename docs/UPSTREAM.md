# Upstream template, and how to port fixes from it

This application was built on the general-purpose agent template at
[cognokratos/simple-agent-template](https://github.com/cognokratos/simple-agent-template).
It is a separate repository, not a branch of it, and that is deliberate: the two
share roughly a quarter of their files and disagree on a security default, so
neither can be the other's configuration.

Splitting them does not sever the link. The template is still upstream, and its
fixes are still worth taking — deliberately, one at a time, rather than by
replaying this whole application on top of a moving base.

## Add the remote once

```bash
git remote add upstream git@github.com:cognokratos/simple-agent-template.git
git fetch upstream
```

## Port a specific fix

Cherry-pick it. The shared files are shared byte-for-byte, so a fix that lands
only in them applies cleanly:

```bash
git log --oneline upstream/main          # find the commit
git cherry-pick -x <sha>                 # -x records the origin in the message
```

`-x` matters: it appends `(cherry picked from commit <sha>)`, which is the only
durable record of what has already been taken. Without it, deciding whether a
given upstream fix is present becomes archaeology.

If the fix touches a file this application rewrote, do not force the
cherry-pick. Read the upstream diff and reimplement the intent here. The
rewritten files are rewritten because the domain differs, not because they
drifted.

## What is actually shared

Four areas are still identical to upstream and are the ones worth watching,
because they are the security- and observability-critical parts that this
application deliberately did not touch:

| Area | Files |
| --- | --- |
| Trace pipeline | `agent/src/nat_streaming_react/observability/*` |
| Agent plumbing | `fastapi_worker.py`, `guardrails_compat.py`, `llm_config.py`, `provenance.py` |
| Gateway core | `gateway/src/http.rs`, `gateway/src/state.rs` |
| UI auth routes | `ui/app/api/gateway/_proxy.ts`, `auth/{callback,login,session}/route.ts` |

Plus `observability/otel-collector.yml`, the evaluation runner/dataset loaders,
and several `scripts/verify_*.py` checks.

That list will drift. Compute the current truth instead of trusting it:

```bash
# files identical to upstream (safe to cherry-pick into)
git fetch upstream
comm -12 <(git ls-tree -r --name-only HEAD | sort) \
         <(git ls-tree -r --name-only upstream/main | sort) |
while read -r f; do
  [ "$(git rev-parse HEAD:"$f")" = "$(git rev-parse upstream/main:"$f")" ] && echo "$f"
done
```

Swap `=` for `!=` to list the divergent ones — the files where an upstream fix
needs reimplementing rather than applying.

## Do not rebase this application onto upstream

It has been tried. The template and this application independently renamed the
same base identifiers — realm, cookie names, crate names, database and volume
names — so a rebase conflicts on roughly fifteen files for no semantic reason at
all, every time, in `gateway/src/config.rs`, `cookies.rs`, `oidc.rs`, both
Dockerfiles and the UI routes. The conflicts carry no information and resolving
them teaches you nothing. Cherry-pick the commit you want instead.

## The longer-term fix

Cherry-picking manages duplication; it does not remove it. If a second
application is ever built on this template, the shared core should be extracted
into versioned artifacts — a Python package for `nat_streaming_react`, a
workspace crate for the gateway core — so each application depends on a version
rather than on a diff. Until then, the table above is the duplication, and this
document is how it is kept honest.
