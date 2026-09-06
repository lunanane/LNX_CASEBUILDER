# Hosting hwcase for other people

Run locally, hwcase is a single-user tool and the files in `backend/scenes`
*are* the project: text you can diff, commit next to the firmware, and edit in
something else. That is the right shape for one person and exactly the wrong
shape for a public instance, where the same directory becomes one shared drawer
that every visitor can list, rename and overwrite — and where two people both
naming a scene `case` take turns destroying each other's work.

So a public instance does not have one. `HWCASE_HOSTED=1` puts the server into
a mode where **it keeps nothing**: no scene directory, no writable part
library, no output files. The project lives in the browser, and leaves it as a
`.yaml` file the user keeps.

This costs less than it sounds like, because the engine was already stateless.
Every route that does real work — `/api/resolve`, `/api/build`,
`/api/export/svg`, `/api/export/dxf`, `/api/export/sheets`, `/api/render` —
takes the whole scene in the request body and reads no file to answer it. The
only thing hosted mode removes is the drawer.

There are no accounts, and nothing to back up.

---

## Looking at it before you deploy it

```
start-hosted.bat
```

The same launcher as `start.bat`, with `HWCASE_HOSTED=1` set — it sets that one
variable and calls straight back into `start.bat`, so there is no second copy
of the bootstrap to drift out of step. It comes up on **8497** rather than
8487, so your own editor can stay open beside it and a bookmark keeps meaning
what it meant.

Worth doing before every deploy, because the two modes differ in ways that stay
invisible until you hit them:

- your scenes in `backend/scenes` are not there, and cannot be — the routes
  that would list them do not exist;
- a board imported from the catalogue goes into the scene file rather than the
  palette, and is gone if the scene is not saved;
- a second render is refused while the first is running;
- closing the tab is not automatically safe, because the project is in
  `localStorage` rather than on disk.

None of those is worth meeting for the first time on the live instance.

---

## Getting one running

```sh
HWCASE_SITE=case.example.com docker compose up -d --build
```

Caddy takes a certificate for that name on its own. Leave `HWCASE_SITE` unset
and it serves `http://localhost:8080` with no certificate, which is what you
want while you are still deciding.

Without compose:

```sh
docker build -t hwcase .
docker run --rm -p 8000:8000 -e HWCASE_HOSTED=1 hwcase
```

The photographic renderer is off by default because Mitsuba is about 100 MB and
is the only reason the image is not small. Turn it on at build time:

```sh
docker build --build-arg WITH_RENDER=1 -t hwcase .
```

Everything else works without it; `/api/config` reports whether it is there,
and the editor hides the panel when it is not.

### Every change after the first

```sh
./deploy/update.sh --pull
```

Fetch, rebuild, restart, and then **wait for the new container to answer before
calling it done**. That last step is the only interesting part: a build can
succeed and still be broken — a file missing from the image, a dependency that
resolved differently this month — and without the check the container that
worked is already gone by the time anybody notices. If it does not come up, the
script prints the logs and stops.

Rolling back is deploying an older commit; there is no state to unwind, because
a hosted instance keeps none. Check out the revision that worked and run it
again.

---

## What a user's project actually is

Two places, and it is worth being straight with people about both.

**The browser.** Scenes are kept in `localStorage`, keyed per origin. This is
the working copy: it survives a reload and a closed tab, and it is what makes
the editor usable without asking anyone to save every thirty seconds. It does
not survive clearing site data, and it does not exist at all in a private
window — where the editor still runs but says so, once, in the status line.

**A file.** `file → save to a file…` writes the scene as YAML. In Chrome and
Edge the browser hands back a real handle, so the file is picked once and every
later save writes straight back into it, in place. Firefox and Safari have no
such picker, so it downloads instead. `file → open a file…` reads one back.

The file is the durable artifact and the thing to tell people to keep. It is
also self-contained: a scene carries any parts imported from the vendor
catalogue inside itself (`Scene.parts`), so handing somebody your `.yaml` opens
on their machine with the right board in it rather than a red *unknown part*.

Comments survive the round trip. The editor never serialises the model itself —
it posts the scene *and the text it opened* to `/api/scene/serialize`, and the
server merges the new values into the existing document. The reasoning written
into a scene file is half of what the file is worth, and a save that quietly
deleted it would be a bug, not a simplification.

---

## What it costs to run

Geometry is CPU work and Python holds the GIL through most of it. A resolve on
a busy scene is tens of milliseconds; a full build with a carved interior is
more like a second. A handful of people dragging boards around is
unremarkable. A hundred is not, and the answer there is more workers, not a
bigger box — raise `--workers` in the Dockerfile's `CMD` (and the container's
CPU limit in `docker-compose.yml` with it, or the workers just contend).

The renderer is the one genuinely expensive thing, and it is capped rather than
queued. A render is minutes of every core the machine has, so hosted mode runs
**one at a time per worker** and returns `429` with a `Retry-After` to the
second person who asks. Raise `HWCASE_RENDER_SLOTS` and `--workers` together or
not at all — each worker gets its own semaphore, so raising one without the
other silently multiplies what a single visitor can order.

| variable | hosted default | what it is |
| --- | --- | --- |
| `HWCASE_HOSTED` | — | `1` turns all of this on. Unset is the local tool. |
| `HWCASE_RENDER_SLOTS` | `1` | Renders at once, per worker. |
| `HWCASE_MAX_SAMPLES` | `512` | Ceiling on render quality a request may ask for. |
| `HWCASE_MAX_PIXELS` | `1920` | Ceiling on either image dimension. |
| `HWCASE_ORIGINS` | none | Comma-separated CORS origins. Unset means same-origin only, which is correct when the editor is served by this same app. |
| `HWCASE_MAX_SCENE_BYTES` | `1048576` | Largest scene *file* the two text routes will read. Twenty times the biggest scene in this repo. |

Locally those last four default to something generous (4096 samples, four
render slots, `*` for CORS) because the only person they can cost anything is
the person who typed them.

The proxy caps request bodies at 8 MB, which is two orders of magnitude more
than a scene needs and the difference between a bad request and a bad day.

`/api/scene/parse` and `/api/scene/serialize` take a file from anybody who can
reach the port, so they also refuse YAML anchors outright. `safe_load` builds
no arbitrary objects, but it does expand aliases, and seven hundred bytes of
nested `&d [*c, *c, *c, *c]` becomes trillions of list elements — the worker,
gone. No scene file has ever contained an anchor, so refusing them costs
nothing; counting them would not have worked, because the depth that hurts
arrives in twenty lines.

---

## What is shared, and why that is all right

Two caches are shared by everybody, and neither is anyone's data:

- `vendor/catalog` — the Adafruit product list, the same five and a half
  thousand items for every visitor, refreshed daily.
- `vendor/cad` — vendor models downloaded on demand while measuring a part.

Both are mounted as a volume in `docker-compose.yml` so they survive a restart.
Losing them costs one slow search, which is why they are a volume rather than a
backup.

Importing a part is where hosted and local genuinely differ. Locally, an import
is filed into `backend/parts/imported.yaml` and joins the palette permanently.
Hosted, it is measured and handed straight back — one visitor importing a board
must not change what every other visitor sees — and the browser puts it into
the scene, where it is saved into that user's own file.

---

## What this deliberately is not

No accounts, no sharing, no server-side history, no project list. Adding any of
them means giving the server somewhere to put things, and that is a different
program: per-user scene roots, a job queue for renders and exports, quotas, and
an answer for what happens when somebody's storage fills up.

If you want that, the seam to build on is already here —
`backend/hwcase/api.py` splits its routes into the ones that touch disk (the
`disk` router, absent when hosted) and the ones that do not. Multi-tenancy is
the disk router coming back with a user attached to every path.

If what you actually want is for people to run it themselves rather than for
you to run it for them, that is a smaller thing than either: hand them the
Dockerfile. The same image, with hosted mode switched back off and a directory
mounted where the scenes go, is the local tool with their project in it — one
user, real files, no hosting:

```sh
docker run --rm -p 8000:8000 -e HWCASE_HOSTED= -v "$PWD/my-project:/app/backend/scenes" hwcase
```

The image sets `HWCASE_HOSTED=1`, so it has to be cleared explicitly. The
mounted directory has to be writable by uid 10001, which is the unprivileged
user the container runs as.
