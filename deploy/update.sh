#!/usr/bin/env sh
# Put the current checkout on the instance.
#
#   ./deploy/update.sh           build and restart from the working tree
#   ./deploy/update.sh --pull    fetch master first, then do that
#
# The point of the health gate at the end is that a build can succeed and still
# be broken -- a missing file in the image, a typo in an import, a dependency
# that resolved differently this month. Without the gate the old container is
# already gone by the time you find out, and finding out means somebody trying
# to use it. With it, the script tells you, and you roll back the same way you
# deployed: check out the commit that worked and run this again.
#
# Nothing here is stateful. There is no database to migrate and no user data to
# preserve, because a hosted instance keeps neither -- see docs/hosting.md.
# The only volume is a cache of the Adafruit catalogue, and it survives.

set -eu

cd "$(dirname "$0")/.."

if [ "${1:-}" = "--pull" ]; then
    echo "==> fetching"
    git pull --ff-only
fi

echo "==> at $(git rev-parse --short HEAD) $(git log -1 --format=%s)"

echo "==> building"
docker compose build

echo "==> restarting"
docker compose up -d

# Poll the app directly rather than through Caddy: this has to work the same
# whether the proxy is serving a plain port or a certificate for a domain, and
# it must not be satisfied by Caddy answering while the thing behind it is
# still on fire.
echo "==> waiting for it to answer"
i=0
while [ "$i" -lt 30 ]; do
    if docker compose exec -T hwcase python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" \
        >/dev/null 2>&1; then
        echo "==> up"
        docker compose exec -T hwcase python -c \
            "import json,urllib.request; print('   ', json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/config')))"
        exit 0
    fi
    i=$((i + 1))
    sleep 2
done

echo "!!! it did not come up within a minute. The logs:"
docker compose logs --tail 40 hwcase
echo
echo "!!! to go back: git checkout <the commit that worked> && ./deploy/update.sh"
exit 1
