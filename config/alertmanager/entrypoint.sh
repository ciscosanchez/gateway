#!/bin/sh
# Render alertmanager.yml.tpl -> /tmp/alertmanager.yml with env substitution,
# then exec the alertmanager binary. Only ${ALERTMANAGER_SLACK_WEBHOOK} and
# ${ALERTMANAGER_PAGERDUTY_KEY} are substituted — Go-template braces ({{ ... }})
# used by alertmanager itself are preserved.
set -eu

TPL=/etc/alertmanager/alertmanager.yml.tpl
OUT=/tmp/alertmanager.yml

# Substitute only the variables we know about, so alertmanager's own Go-template
# syntax survives unchanged.
sed \
  -e "s#\${ALERTMANAGER_SLACK_WEBHOOK}#${ALERTMANAGER_SLACK_WEBHOOK:-}#g" \
  -e "s#\${ALERTMANAGER_PAGERDUTY_KEY}#${ALERTMANAGER_PAGERDUTY_KEY:-}#g" \
  "$TPL" > "$OUT"

exec /bin/alertmanager "$@"
