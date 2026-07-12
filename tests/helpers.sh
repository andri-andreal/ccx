# shellcheck shell=bash
# tests/helpers.sh — dependency-free assertions
TESTS_RUN=0
TESTS_FAIL=0

_pass() { printf 'ok   %s\n' "$1"; }
_fail() { TESTS_FAIL=$((TESTS_FAIL + 1)); printf 'FAIL %s\n' "$1" >&2; }

assert_eq() {
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ "$1" = "$2" ]; then _pass "${3:-eq}"; else _fail "${3:-eq}: expected [$2] got [$1]"; fi
}
assert_contains() {
  TESTS_RUN=$((TESTS_RUN + 1))
  case "$1" in *"$2"*) _pass "${3:-contains}" ;; *) _fail "${3:-contains}: [$1] lacks [$2]" ;; esac
}
assert_not_contains() {
  TESTS_RUN=$((TESTS_RUN + 1))
  case "$1" in *"$2"*) _fail "${3:-not_contains}: [$1] still has [$2]" ;; *) _pass "${3:-not_contains}" ;; esac
}
assert_exit() {
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ "$1" -eq "$2" ]; then _pass "${3:-exit}"; else _fail "${3:-exit}: expected $2 got $1"; fi
}
assert_mode() {
  TESTS_RUN=$((TESTS_RUN + 1))
  m="$(stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1")"
  if [ "$m" = "$2" ]; then _pass "${3:-mode}"; else _fail "${3:-mode}: $1 is $m not $2"; fi
}
finish() {
  printf '\n%d assertions, %d failed\n' "$TESTS_RUN" "$TESTS_FAIL"
  [ "$TESTS_FAIL" -eq 0 ]
}
