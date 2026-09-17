#!/usr/bin/env bash
#
# Delete untagged container image versions from GHCR.
#
# Every rebuild moves a tag such as `latest` to a new digest, which leaves the
# previous version behind without any tag. Those orphaned versions keep their
# layers and add up over time, so this script walks the CodeMate image packages
# and deletes the untagged versions that are older than a configurable age.
#
# Environment variables:
#   OWNER            GitHub user or organization that owns the packages (required)
#   PACKAGES         Space separated package names to clean
#                    (default: "codemate codemate-base codemate-pure")
#   OLDER_THAN_DAYS  Minimum age in days before an untagged version is deleted
#                    (default: 15)
#   DRY_RUN          "true" to report what would be deleted without deleting
#                    (default: false)
#   GH_TOKEN         Token with `packages: write` on the owner (used by `gh`)
#
set -euo pipefail

log() { printf '%s\n' "$*"; }
warn() { printf '[warn] %s\n' "$*" >&2; }

owner=${OWNER:?OWNER must be set}
packages=${PACKAGES:-codemate codemate-base codemate-pure}
older_than_days=${OLDER_THAN_DAYS:-15}
dry_run=${DRY_RUN:-false}

case "$older_than_days" in
  '' | *[!0-9]*)
    warn "OLDER_THAN_DAYS must be a non-negative integer, got '$older_than_days'"
    exit 2
    ;;
esac

# Versions created before this instant are old enough to be deleted.
cutoff_epoch=$(($(date -u +%s) - older_than_days * 86400))

owner_type=$(gh api "users/$owner" --jq .type)
case "$owner_type" in
  Organization) api_base="orgs/$owner" ;;
  User) api_base="users/$owner" ;;
  *)
    warn "cannot determine owner type for '$owner' (got '$owner_type')"
    exit 1
    ;;
esac

is_older_than_cutoff() {
  # created_at looks like 2026-09-16T15:03:41Z.
  [ "$(date -u -d "$1" +%s)" -lt "$cutoff_epoch" ]
}

deleted=0
kept=0
failed=0

for package in $packages; do
  log "==> $owner/$package: untagged versions older than ${older_than_days} days"

  versions=$(
    gh api --paginate "$api_base/packages/container/$package/versions?per_page=100" \
      --jq '.[] | [.id, .created_at, (.metadata.container.tags | join(","))] | @tsv'
  ) || {
    warn "failed to list versions of $package"
    failed=$((failed + 1))
    continue
  }

  while IFS=$'\t' read -r id created tags; do
    [ -n "${id:-}" ] || continue

    if [ -n "$tags" ]; then
      kept=$((kept + 1))
      continue
    fi

    if ! is_older_than_cutoff "$created"; then
      log "    keep   $id (untagged, created $created, not older than ${older_than_days}d)"
      kept=$((kept + 1))
      continue
    fi

    if [ "$dry_run" = "true" ]; then
      log "    would delete $id (untagged, created $created)"
      deleted=$((deleted + 1))
      continue
    fi

    if gh api -X DELETE "$api_base/packages/container/$package/versions/$id" >/dev/null; then
      log "    delete $id (untagged, created $created)"
      deleted=$((deleted + 1))
    else
      warn "    failed to delete $id"
      failed=$((failed + 1))
    fi
  done <<< "$versions"
done

verb="deleted"
[ "$dry_run" = "true" ] && verb="would delete"
log ""
log "$verb $deleted untagged version(s), kept $kept tagged/recent version(s)"

if [ "$failed" -gt 0 ]; then
  warn "$failed version(s) could not be handled"
  exit 1
fi
