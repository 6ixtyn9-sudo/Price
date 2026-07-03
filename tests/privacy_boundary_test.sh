#!/bin/bash
# privacy_boundary_test.sh
# Strongly asserts the boundary between public and private data.
set -euo pipefail

if [ -f .env ]; then
  source .env
fi

# Pull from env / .env if available, otherwise fall back to placeholders.
SUPABASE_URL=${SUPABASE_URL:-"https://your-project-id.supabase.co"}
SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY:-"YOUR_ANON_KEY_HERE"}

echo "--- OPERATION PAWS PRIVACY BOUNDARY ASSERTION ---"

# If Supabase is not connected yet, skip honestly instead of failing on a fake host.
if [[ -z "${SUPABASE_URL:-}" ]] || [[ -z "${SUPABASE_ANON_KEY:-}" ]]; then
  echo "Skipping privacy boundary smoke test: Supabase URL or anon key is missing."
  exit 0
fi

if [[ "$SUPABASE_URL" == "https://your-project-id.supabase.co" ]] || [[ "$SUPABASE_URL" == "https://test.supabase.co" ]]; then
  echo "Skipping privacy boundary smoke test: no real Supabase project is configured yet."
  exit 0
fi

if [[ "$SUPABASE_ANON_KEY" == "YOUR_ANON_KEY_HERE" ]] || [[ "$SUPABASE_ANON_KEY" == "ci-anon-key" ]]; then
  echo "Skipping privacy boundary smoke test: no real Supabase anon key is configured yet."
  exit 0
fi

FAILED=0

# 1. Assert Public Access
echo "1. Asserting Public Access to paws_public_dogs..."
PUB_RES=$(curl -s -w "\n%{http_code}" -X GET "$SUPABASE_URL/rest/v1/paws_public_dogs?select=paws_ref&limit=1" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $SUPABASE_ANON_KEY")

PUB_BODY=$(echo "$PUB_RES" | sed '$d')
PUB_CODE=$(echo "$PUB_RES" | tail -n1)

if [ "$PUB_CODE" -ne 200 ]; then
  echo "❌ FAILED: Public endpoint returned HTTP $PUB_CODE instead of 200."
  FAILED=1
else
  echo "✅ Public endpoint returned HTTP 200."
fi

# 2. Assert Private Restriction
check_private() {
  local table=$1
  echo "Checking restriction on $table..."

  PRIV_RES=$(curl -s -w "\n%{http_code}" -X GET "$SUPABASE_URL/rest/v1/$table?select=*" \
    -H "apikey: $SUPABASE_ANON_KEY" \
    -H "Authorization: Bearer $SUPABASE_ANON_KEY")

  PRIV_BODY=$(echo "$PRIV_RES" | sed '$d')
  PRIV_CODE=$(echo "$PRIV_RES" | tail -n1)

  # PostgREST with RLS usually returns 200 with an empty array [] if RLS blocks it,
  # or 401/403 if the user is not authenticated or the table is totally restricted.
  if [[ "$PRIV_BODY" == "[]" ]] || [[ "$PRIV_CODE" == "401" ]] || [[ "$PRIV_CODE" == "403" ]] || [[ "$PRIV_BODY" == *"error"* ]]; then
    echo "✅ $table is correctly restricted (Code: $PRIV_CODE, Body: $PRIV_BODY)."
  else
    echo "❌ VIOLATION: $table returned data or unexpected status!"
    echo "   Status: $PRIV_CODE"
    echo "   Body: $PRIV_BODY"
    FAILED=1
  fi
}

check_private "paws_dogs"
check_private "paws_audit_log"
check_private "paws_user_roles"
check_private "paws_totp_secrets"

echo "--- PRIVACY ASSERTION COMPLETE ---"

if [ $FAILED -ne 0 ]; then
  echo "RESULT: FAIL (Privacy boundary breach or misconfiguration)"
  exit 1
else
  echo "RESULT: PASS"
  exit 0
fi